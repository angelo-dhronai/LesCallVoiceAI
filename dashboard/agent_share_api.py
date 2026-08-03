"""
Share an agent by link — the recipient IMPORTS an independent copy.

The product rule this module exists to enforce, stated once:

    An imported agent has NO relationship to the agent it came from.

It is a new folder, a new globally-unique slug, a new agent_id, a fresh
revision history, owned by the importer. Editing either side never touches the
other. Archiving the source does not break the copy. Nothing in the call path
ever reads the tables below — `agent_shares` / `agent_share_imports` are the
sharing control plane and the abuse trail, not a runtime join.

Why this is a separate router from agent_builder_api
----------------------------------------------------
That router's `_tenancy_dep` 404s any `{name}` path param the caller does not
own — correct there, fatal here: the whole point of a share link is that
someone who does NOT own the agent reads it. So the preview/import routes are
keyed by an unguessable TOKEN rather than by slug, and this module does its
own owner checks explicitly at each site.

The unit of sharing is the FAMILY
---------------------------------
Agents come in twin pairs (`acme-inbound` + `acme-outbound`); the plan cap
counts families, and the card in the UI is a family. So one link carries the
whole family and one import consumes exactly one agent slot. Whichever
direction variants exist at import time are what the recipient gets.

What is deliberately NOT copied
-------------------------------
* `routing.yaml`  — the sharer's DID binding. Copying it would point the
  importer's brand-new agent at someone else's phone number.
* `bookings.json` — the sharer's live appointments, i.e. their customers.
* `is_active`     — an import must never change which agent answers YOUR
  calls (see _deactivate below).
Everything else (agent.yaml, and the uploaded knowledge-base documents with
their embeddings) travels, because otherwise the imported agent references
knowledge it does not have and reads as broken.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger("agent_share_api")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CALLING_AGENT = _REPO_ROOT / "calling-agent"
if str(_CALLING_AGENT) not in sys.path:
    sys.path.insert(0, str(_CALLING_AGENT))

from backend.app.agent_definition.schema import AgentDefinition   # noqa: E402
from backend.app.agent_definition.registry import agent_registry  # noqa: E402

import agent_builder_api as builder  # noqa: E402  (slug algebra + the ONE write path)

_AGENTS_DIR = builder._AGENTS_DIR
_ARCHIVE_DIR = _AGENTS_DIR / "_archived"

# Default link lifetime. A link that outlives the reason it was created is a
# standing invitation to copy an agent that has since gained real pricing and
# real customer knowledge, so it expires unless the sharer says otherwise.
SHARE_TTL_DAYS = int(os.environ.get("LESCALL_SHARE_TTL_DAYS", "30"))

# Abuse ceiling on imports per account per rolling 24h. Not a product limit —
# the plan cap is the product limit. This stops a script from churning
# create/archive cycles to fill the disk with agent folders.
MAX_IMPORTS_PER_DAY = int(os.environ.get("LESCALL_MAX_IMPORTS_PER_DAY", "20"))

_SLUG_SAFE = re.compile(r"[^a-z0-9_-]+")
# agent_id sits at column 0 in every generated agent.yaml, so a line scan
# avoids parsing ~30 full YAML documents just to check one string.
_AGENT_ID_RE = re.compile(r"^agent_id:\s*['\"]?([^'\"\n]+)", re.M)


# ── request context ─────────────────────────────────────────────────────────

async def _session_dep(request: Request):
    """Every route here requires a logged-in account.

    MUST stay async for the same reason builder._tenancy_dep does: a sync
    dependency runs in a threadpool and its ContextVar write would not reach
    the handler's task. The builder's ContextVar is set too, because
    write_agent_dir() and friends read it.
    """
    import auth
    owner = auth.normalize_owner(auth.current_owner(request))
    builder._REQ_OWNER.set(owner)
    request.state.owner = owner


router = APIRouter(dependencies=[Depends(_session_dep)])


def _owner(request: Request) -> str:
    return getattr(request.state, "owner", "") or builder._ctx_owner()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ── availability ────────────────────────────────────────────────────────────
#
# agent_shares lives in a versioned migration (0017), which is Postgres-only
# and applied by hand. On a box where it has not run yet, every route here
# would raise an opaque 500 mentioning a missing relation. Say so instead.

_TABLES_READY = False


def _assert_shares_ready() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    try:
        from shared.db.database import get_db
        with get_db() as db:
            db.execute("SELECT 1 FROM agent_shares LIMIT 1").fetchone()
        _TABLES_READY = True
    except Exception as exc:
        if re.search(r"does not exist|no such table|undefined", str(exc), re.I):
            raise HTTPException(
                status_code=503,
                detail="Agent sharing is not enabled on this server yet "
                       "(migration 0017 has not been applied).")
        raise


# ── helpers: the family on disk ─────────────────────────────────────────────

def _assert_slug_shape(slug: str) -> str:
    """Reject anything that is not a plain agent slug.

    The ownership check below would already stop a traversal (`../../etc` can
    never match a row in the agents table), but a slug reaches
    `_AGENTS_DIR / slug` before that check runs, so the shape is validated
    where it enters rather than relied upon downstream.
    """
    if not slug or len(slug) > 64 or not slug.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400,
                            detail="'slug' must be alphanumeric with _ or -")
    return slug


def _family_of(slug: str) -> str:
    return builder.agent_family(_assert_slug_shape(slug))


def _family_slugs_on_disk(family: str) -> list[str]:
    """Live slugs of `family`, inbound first. A standalone (pre-twin) agent is
    its own family and appears under its bare slug."""
    out = []
    for candidate in (f"{family}-inbound", f"{family}-outbound", family):
        if candidate in out:
            continue
        if (_AGENTS_DIR / candidate / "agent.yaml").exists():
            out.append(candidate)
    return out


def _owned_family_slugs(family: str, owner: str) -> list[str]:
    """Slugs of `family` that exist on disk AND are owned, unarchived, by
    `owner`. Both halves must hold: a folder with no row is unclaimed
    infrastructure, and an archived row is a deleted agent."""
    on_disk = _family_slugs_on_disk(family)
    if not on_disk:
        return []
    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute(
            "SELECT slug FROM agents WHERE owner=? AND archived_at IS NULL",
            (owner,)).fetchall()
    owned = {r["slug"] for r in rows}
    return [s for s in on_disk if s in owned]


def _slug_is_live_for(slug: str, owner: str) -> bool:
    if not (_AGENTS_DIR / slug / "agent.yaml").exists():
        return False
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute(
                "SELECT 1 FROM agents WHERE slug=? AND owner=? "
                "AND archived_at IS NULL", (slug, owner)).fetchone()
        return bool(row)
    except Exception:
        return False


# ── helpers: minting a name nobody else holds ───────────────────────────────

def _sanitize_family(name: str) -> str:
    base = _SLUG_SAFE.sub("-", (name or "").strip().lower()).strip("-_")
    base = re.sub(r"-{2,}", "-", base)[:40].strip("-_")
    return base or "agent"


def _family_free(family: str) -> bool:
    """True when NOTHING anywhere holds this family's slugs.

    Stricter than the builder's `_assert_slug_free`, which lets a tenant
    re-claim an archived slug. An import must never resurrect a slug that
    belongs to someone else's archive, and it must never collide with a folder
    that exists without a row — so this checks the live tree, the archive tree
    AND every agents row regardless of owner or archived state.
    """
    slugs = [family, f"{family}-inbound", f"{family}-outbound"]
    for slug in slugs:
        if (_AGENTS_DIR / slug).exists() or (_ARCHIVE_DIR / slug).exists():
            return False
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute(
                "SELECT 1 FROM agents WHERE slug=? OR slug=? OR slug=? LIMIT 1",
                tuple(slugs)).fetchone()
        return row is None
    except Exception as exc:
        # Fail CLOSED: if we cannot prove the name is free, do not take it.
        logger.error("[share] family availability check failed for %s: %r",
                     family, exc)
        return False


def _mint_family(source_family: str) -> str:
    base = _sanitize_family(source_family)
    if _family_free(base):
        return base
    for n in range(2, 51):
        candidate = f"{base}-{n}"
        if _family_free(candidate):
            return candidate
    for _ in range(20):  # pathological: fall back to randomness
        candidate = f"{base}-{secrets.token_hex(3)}"
        if _family_free(candidate):
            return candidate
    raise HTTPException(
        status_code=409,
        detail="Could not find a free name for the imported agent. Rename or "
               "archive one of your existing agents and try again.")


def _existing_agent_ids() -> set[str]:
    ids: set[str] = set()
    for path in _AGENTS_DIR.glob("*/agent.yaml"):
        try:
            match = _AGENT_ID_RE.search(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if match:
            ids.add(match.group(1).strip())
    return ids


def _unique_agent_id(preferred: str, taken: set[str]) -> str:
    """agent_id must be unique platform-wide.

    documents/store.get_folder_by_agent_id() resolves an agent's document
    folder by scanning agent.yaml files for a matching agent_id and returning
    the FIRST hit. Two agents sharing an id therefore means one tenant's RAG
    lookups can land in another tenant's document folder — so an import always
    remints, and verifies.
    """
    if preferred not in taken:
        return preferred
    for n in range(2, 100):
        candidate = f"{preferred}-{n}"
        if candidate not in taken:
            return candidate
    return f"{preferred}-{secrets.token_hex(4)}"


# ── helpers: the copied definition ──────────────────────────────────────────

# Fields that describe the SOURCE record's history, not its behaviour. They
# must not travel: a copy that arrives at "revision 47, updated_by
# someone@else.com" lies about its own history and makes the first real edit
# look like a rollback.
_HISTORY_FIELDS = ("revision", "updated_at", "updated_by", "created_at",
                   "_rev", "audit")


def _prepare_spec(source_yaml: Path, new_slug: str, taken_ids: set[str]) -> dict:
    spec = yaml.safe_load(source_yaml.read_text(encoding="utf-8")) or {}
    if not isinstance(spec, dict):
        raise HTTPException(status_code=422,
                            detail="The shared agent's definition is not readable.")
    for field in _HISTORY_FIELDS:
        spec.pop(field, None)
    spec["agent_id"] = _unique_agent_id(f"{new_slug}-v1", taken_ids)
    taken_ids.add(spec["agent_id"])
    spec["revision"] = 1
    return spec


def _copy_documents(src_slug: str, dst_slug: str) -> int:
    """Copy the knowledge-base documents (text, chunks and .npy embeddings).

    Everything under documents/ is folder-relative — index.json holds doc_ids,
    never paths — so a plain tree copy is a working knowledge base. Returns
    the number of documents copied; a failure here is logged and swallowed
    because a missing PDF must not undo an otherwise-complete import.
    """
    src = _AGENTS_DIR / src_slug / "documents"
    if not src.is_dir():
        return 0
    dst = _AGENTS_DIR / dst_slug / "documents"
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
        index = dst / "index.json"
        if index.exists():
            return len(json.loads(index.read_text(encoding="utf-8")) or [])
        return 0
    except Exception as exc:
        logger.warning("[share] document copy %s -> %s failed: %r",
                       src_slug, dst_slug, exc)
        shutil.rmtree(dst, ignore_errors=True)
        return 0


def _doc_count(slug: str) -> int:
    index = _AGENTS_DIR / slug / "documents" / "index.json"
    if not index.exists():
        return 0
    try:
        return len(json.loads(index.read_text(encoding="utf-8")) or [])
    except Exception:
        return 0


def _deactivate(slug: str) -> None:
    """Imported agents are never the account's active fallback.

    `agents.is_active` defaults to 1, and conversation_call_bridge picks the
    most recently updated is_active row for an owner as the agent for calls
    that match no DID binding. Left alone, importing an agent to look at it
    would silently take over answering that account's unbound calls.
    """
    try:
        from shared.db.database import get_db
        with get_db() as db:
            db.execute("UPDATE agents SET is_active=0 WHERE slug=?", (slug,))
    except Exception as exc:
        logger.error("[share] could not clear is_active for %s: %r", slug, exc)


# ── helpers: link URL ───────────────────────────────────────────────────────

def _public_base(request: Request) -> str:
    explicit = os.environ.get("LESCALL_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    try:
        import auth
        redirect = (auth.OAUTH_REDIRECT_URL or "").strip()
        if redirect.endswith("/auth/callback"):
            return redirect[: -len("/auth/callback")]
    except Exception:
        pass
    return str(request.base_url).rstrip("/")


def _share_url(request: Request, token: str) -> str:
    # /s/<token> is a thin SPA route that forwards to
    # /build/agents?import=<token>. Short links survive being pasted into
    # chat apps that break on long query strings.
    return f"{_public_base(request)}/s/{token}"


# ── helpers: rows ───────────────────────────────────────────────────────────

def _as_dt(value):
    from shared.billing.entitlements import as_dt
    return as_dt(value)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _link_state(row: dict) -> str:
    if row.get("revoked_at"):
        return "revoked"
    expires = _as_dt(row.get("expires_at"))
    if expires and expires <= _now():
        return "expired"
    return "live"


def _link_payload(request: Request, row: dict) -> dict:
    return {
        "token": row["token"],
        "url": _share_url(request, row["token"]),
        "family": row["family"],
        "label": row.get("label") or row["family"],
        "state": _link_state(row),
        "import_count": int(row.get("import_count") or 0),
        "expires_at": row.get("expires_at"),
        "created_at": row.get("created_at"),
    }


def _fetch_share(token: str) -> dict | None:
    from shared.db.database import get_db
    with get_db() as db:
        row = db.execute("SELECT * FROM agent_shares WHERE token=?",
                         (token,)).fetchone()
    return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════
# Sharer side
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/share")
async def get_link(request: Request, slug: str = "") -> dict:
    """The live link for one of MY agents, or {"link": null}."""
    _assert_shares_ready()
    owner = _owner(request)
    if not slug:
        raise HTTPException(status_code=400, detail="'slug' is required")
    family = _family_of(slug)
    if not _owned_family_slugs(family, owner):
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    from shared.db.database import get_db
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM agent_shares WHERE owner=? AND family=? "
            "AND revoked_at IS NULL", (owner, family)).fetchone()
    if not row:
        return {"link": None, "family": family}
    payload = _link_payload(request, dict(row))
    if payload["state"] != "live":
        return {"link": None, "family": family, "previous_state": payload["state"]}
    return {"link": payload, "family": family}


@router.post("/api/share")
async def create_link(request: Request) -> dict:
    """Mint (or return) the share link for one of my agents.

    Idempotent by design: pressing Share twice must hand back the SAME link,
    not a second one that has to be revoked separately. An expired link is
    retired and replaced.
    """
    _assert_shares_ready()
    owner = _owner(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    slug = str(body.get("slug") or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="'slug' is required")

    family = _family_of(slug)
    slugs = _owned_family_slugs(family, owner)
    if not slugs:
        # 404 rather than 403 — never confirm that another tenant's agent exists.
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")

    # Refuse to hand out a link to a definition that no longer loads: the
    # recipient's import would otherwise 422 at them with a message about
    # someone else's YAML, which they can do nothing about.
    try:
        agent = AgentDefinition.from_yaml(_AGENTS_DIR / slugs[0] / "agent.yaml")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"This agent has a configuration error and cannot be "
                   f"shared until it is fixed: {exc}")
    label = agent.identity.agent_name or family

    from shared.db.database import get_db
    with get_db() as db:
        existing = db.execute(
            "SELECT * FROM agent_shares WHERE owner=? AND family=? "
            "AND revoked_at IS NULL", (owner, family)).fetchone()
        if existing:
            row = dict(existing)
            if _link_state(row) == "live":
                # Same link, refreshed label. Nothing else changes — in
                # particular the token stays put, so links already sent keep
                # working.
                db.execute("UPDATE agent_shares SET label=? WHERE id=?",
                           (label, row["id"]))
                row["label"] = label
                return {"link": _link_payload(request, row), "created": False}
            db.execute("UPDATE agent_shares SET revoked_at=CURRENT_TIMESTAMP "
                       "WHERE id=?", (row["id"],))

        token = secrets.token_urlsafe(32)
        expires = (_now() + dt.timedelta(days=SHARE_TTL_DAYS)
                   ).isoformat() if SHARE_TTL_DAYS > 0 else None
        db.execute(
            "INSERT INTO agent_shares (token, owner, family, label, expires_at) "
            "VALUES (?,?,?,?,?)", (token, owner, family, label, expires))
        row = dict(db.execute("SELECT * FROM agent_shares WHERE token=?",
                              (token,)).fetchone())

    try:
        from shared.audit import record
        record(owner, "agent_share_created", target=family,
               meta={"slugs": slugs}, ip=_client_ip(request))
    except Exception:
        pass
    logger.info("[share] link created for %s by %s (%d variants)",
                family, owner, len(slugs))
    return {"link": _link_payload(request, row), "created": True}


@router.delete("/api/share/{token}")
async def revoke_link(token: str, request: Request) -> dict:
    """Kill a link. Copies already imported are unaffected — they are
    independent agents, and taking them back is not a thing sharing can do."""
    _assert_shares_ready()
    owner = _owner(request)
    from shared.db.database import get_db
    with get_db() as db:
        row = db.execute("SELECT * FROM agent_shares WHERE token=? AND owner=?",
                         (token, owner)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Share link not found")
        if not row["revoked_at"]:
            db.execute("UPDATE agent_shares SET revoked_at=CURRENT_TIMESTAMP "
                       "WHERE id=?", (row["id"],))
    try:
        from shared.audit import record
        record(owner, "agent_share_revoked", target=row["family"],
               ip=_client_ip(request))
    except Exception:
        pass
    return {"status": "revoked", "family": row["family"],
            "import_count": int(row["import_count"] or 0)}


# ═══════════════════════════════════════════════════════════════════════════
# Recipient side
# ═══════════════════════════════════════════════════════════════════════════

def _summarize(slugs: list[str]) -> dict:
    """What the popup shows. Read from the CURRENT definition, so the
    recipient previews exactly the bytes they would import."""
    variants, languages, stages = [], [], []
    agent_name = company = ""
    calendar_slots = docs = 0
    for slug in slugs:
        try:
            agent = AgentDefinition.from_yaml(_AGENTS_DIR / slug / "agent.yaml")
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"The shared agent has a configuration error and "
                       f"cannot be imported: {exc}")
        direction = str(getattr(agent, "call_direction", "inbound") or "inbound")
        variants.append({"slug": slug, "direction": direction})
        agent_name = agent_name or agent.identity.agent_name
        company = company or agent.identity.company_name
        for lang in agent.languages.supported:
            if lang not in languages:
                languages.append(lang)
        if not stages:
            stages = [s.id for s in agent.call_flow.stages]
        calendar_slots = max(calendar_slots, len(agent.calendar.mock_slots or []))
        docs += _doc_count(slug)
    return {
        "agent_name": agent_name, "company": company,
        "languages": languages, "stages": stages,
        "directions": [v["direction"] for v in variants],
        "variants": variants, "calendar_slots": calendar_slots,
        "documents": docs,
    }


def _sharer_identity(email: str) -> dict:
    name = ""
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute("SELECT name, company FROM users WHERE email=?",
                             (email,)).fetchone()
        if row:
            name = (row["name"] or row["company"] or "") or ""
    except Exception:
        pass
    return {"email": email, "name": name}


def _previous_import(share_id: int, importer: str) -> dict | None:
    from shared.db.database import get_db
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM agent_share_imports WHERE share_id=? AND importer=?",
            (share_id, importer)).fetchone()
    if not row:
        return None
    try:
        slugs = json.loads(row["new_slugs"] or "[]")
    except Exception:
        slugs = []
    live = [s for s in slugs if _slug_is_live_for(s, importer)]
    return {"id": row["id"], "created_at": row["created_at"],
            "new_family": row["new_family"], "slugs": slugs, "live": live}


def _imports_today(importer: str) -> int:
    try:
        from shared.db.database import get_db
        since = (_now() - dt.timedelta(days=1)).isoformat()
        with get_db() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM agent_share_imports "
                "WHERE importer=? AND created_at >= ?", (importer, since)).fetchone()
        return int((row or {}).get("n") or 0)
    except Exception:
        return 0


def _resolve_for_import(token: str, importer: str) -> tuple[dict, list[str]]:
    """(share row, live source slugs) or the right HTTP error.

    404 for a token that never existed; 410 for one that did but no longer
    works. A recipient staring at "not found" for a link a colleague just sent
    cannot tell those apart, and they need different next steps.
    """
    share = _fetch_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="This share link is not valid.")
    state = _link_state(share)
    if state == "revoked":
        raise HTTPException(
            status_code=410,
            detail="This share link was revoked by the person who created it.")
    if state == "expired":
        raise HTTPException(
            status_code=410,
            detail="This share link has expired. Ask for a fresh one.")
    if share["owner"] == importer:
        raise HTTPException(
            status_code=409,
            detail="This is your own agent — it is already in your account.")
    slugs = _owned_family_slugs(share["family"], share["owner"])
    if not slugs:
        raise HTTPException(
            status_code=410,
            detail="The shared agent is no longer available — its owner "
                   "deleted or archived it.")
    return share, slugs


@router.get("/api/share/{token}")
async def preview_share(token: str, request: Request) -> dict:
    """What the import popup renders. Never mutates anything.

    Deliberately reports refusals in the BODY (can_import=false + reason)
    rather than as an HTTP error for the two cases the user can act on — at
    plan cap, and already imported — because the popup has to render the
    agent's details alongside the explanation. Dead links stay HTTP errors.
    """
    _assert_shares_ready()
    importer = _owner(request)
    share, slugs = _resolve_for_import(token, importer)
    summary = _summarize(slugs)

    can_import, reason, code = True, "", ""
    billing: dict = {}
    try:
        from shared.billing.entitlements import (
            agent_families_used, enforcement_enabled, get_entitlements)
        ent = get_entitlements(importer)
        limit = ent.get("max_agent_families")
        used = agent_families_used(importer)
        billing = {"agents_used": used, "agents_limit": limit,
                   "enforcement": enforcement_enabled()}
        from shared.billing.entitlements import check_agent_create
        decision = check_agent_create(importer)
        if not decision:
            can_import, reason, code = False, decision.reason, decision.code
    except Exception as exc:
        logger.warning("[share] entitlement preview failed for %s: %r",
                       importer, exc)

    previous = _previous_import(share["id"], importer)
    if previous and previous["live"] and can_import:
        can_import = False
        code = "already_imported"
        reason = (f"You already imported this agent as "
                  f"“{previous['live'][0]}”. Open it from your agents list.")

    if can_import and _imports_today(importer) >= MAX_IMPORTS_PER_DAY:
        can_import, code = False, "rate_limited"
        reason = (f"You have imported {MAX_IMPORTS_PER_DAY} agents in the last "
                  f"24 hours. Try again later.")

    return {
        "token": token,
        "agent": summary,
        "shared_by": _sharer_identity(share["owner"]),
        "expires_at": share["expires_at"],
        "can_import": can_import,
        "reason": reason,
        "code": code,
        "billing": billing,
        "already_imported": ({"slugs": previous["live"],
                              "at": previous["created_at"]}
                             if previous and previous["live"] else None),
    }


@router.post("/api/share/{token}/import")
async def import_share(token: str, request: Request) -> dict:
    """Copy the shared agent into MY account as a brand-new, unrelated agent.

    Ordering matters and is not arbitrary:

      1. validate the link, then the plan cap, BEFORE any filesystem write
      2. RESERVE the (share_id, importer) row — the unique index on that pair
         is what makes a double-clicked button, a reloaded tab and two browser
         windows converge on one copy instead of three
      3. create the folders + ownership rows, rolling every one of them back
         together if any single step fails (half an imported twin pair is
         worse than none: a number could bind to the survivor and run
         wrong-direction prompts)
      4. only then record what it became and bump the counter
    """
    _assert_shares_ready()
    importer = _owner(request)
    share, source_slugs = _resolve_for_import(token, importer)

    previous = _previous_import(share["id"], importer)
    if previous and previous["live"]:
        raise HTTPException(status_code=409, detail={
            "error": "already_imported",
            "slugs": previous["live"],
            "message": f"You already imported this agent as "
                       f"“{previous['live'][0]}”."})
    if _imports_today(importer) >= MAX_IMPORTS_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"You have imported {MAX_IMPORTS_PER_DAY} agents in the "
                   f"last 24 hours. Try again later.")

    # Validate every source definition up front — a 422 after three folders
    # exist is a mess to unwind for no reason.
    _summarize(source_slugs)

    # E7 pre-check before ANY filesystem write. The authoritative re-check runs
    # under an advisory lock inside _claim_slug (via write_agent_dir), which is
    # what makes two concurrent imports at a cap of 1 resolve to one agent.
    builder._assert_agent_quota(importer)

    new_family = _mint_family(share["family"])
    standalone = len(source_slugs) == 1 and source_slugs[0] == share["family"]

    from shared.db.database import get_db
    with get_db() as db:
        if previous:  # stale row: the earlier copy is archived or gone
            db.execute("DELETE FROM agent_share_imports WHERE id=?",
                       (previous["id"],))
        try:
            db.execute(
                "INSERT INTO agent_share_imports (share_id, importer, "
                "source_owner, source_family, new_family, new_slugs) "
                "VALUES (?,?,?,?,?,?)",
                (share["id"], importer, share["owner"], share["family"],
                 new_family, "[]"))
        except Exception as exc:
            if re.search(r"unique|duplicate", str(exc), re.I):
                raise HTTPException(
                    status_code=409,
                    detail="This agent is already being imported into your "
                           "account. Refresh your agents list.")
            raise

    created: list[str] = []
    taken_ids = _existing_agent_ids()
    try:
        for src in source_slugs:
            direction = None
            for d in builder.DIRECTIONS:
                if src.endswith(f"-{d}"):
                    direction = d
            new_slug = new_family if standalone else f"{new_family}-{direction}"
            spec = _prepare_spec(_AGENTS_DIR / src / "agent.yaml", new_slug,
                                 taken_ids)
            builder.write_agent_dir(new_slug, spec, importer)
            created.append(new_slug)
            # write_agent_dir claims the slug with ON CONFLICT DO NOTHING, so
            # a lost race leaves the folder written but owned by someone else.
            # Prove ownership rather than assume it.
            if builder._slug_owner(new_slug) != importer:
                raise HTTPException(
                    status_code=409,
                    detail="That agent name was taken while importing. "
                           "Try again.")
            _deactivate(new_slug)
            _copy_documents(src, new_slug)
    except Exception:
        for slug in created:
            shutil.rmtree(_AGENTS_DIR / slug, ignore_errors=True)
            agent_registry.invalidate(slug)
            builder._unclaim_slug(slug, importer)
        try:
            with get_db() as db:
                db.execute("DELETE FROM agent_share_imports WHERE share_id=? "
                           "AND importer=?", (share["id"], importer))
        except Exception as exc:
            logger.error("[share] could not release import reservation "
                         "(%s -> %s): %r", token, importer, exc)
        raise

    with get_db() as db:
        db.execute(
            "UPDATE agent_share_imports SET new_family=?, new_slugs=? "
            "WHERE share_id=? AND importer=?",
            (new_family, json.dumps(created), share["id"], importer))
        db.execute("UPDATE agent_shares SET import_count=import_count+1 "
                   "WHERE id=?", (share["id"],))

    try:
        from shared.audit import record
        record(importer, "agent_share_imported", target=new_family,
               meta={"source_owner": share["owner"],
                     "source_family": share["family"], "slugs": created},
               ip=_client_ip(request))
    except Exception:
        pass

    primary = created[0]
    logger.info("[share] %s imported %s from %s as %s (%s)",
                importer, share["family"], share["owner"], new_family,
                ", ".join(created))
    return {"status": "imported", "family": new_family, "slugs": created,
            "primary": primary}
