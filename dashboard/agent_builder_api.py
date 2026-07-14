"""
Agent Builder API — mounted into the LesCall dashboard (dashboard/server.py).

This exposes the data-driven Agent Builder control plane (create / edit / raw
YAML / routing / documents+RAG / validate / intent-preview / TTS-preview /
activate) on the SAME FastAPI app + port (8080) the React dashboard already
talks to, so the builder lives on the main dashboard with no extra service.

Design notes
------------
* Reuses the real runtime modules under calling-agent/backend/app — there is
  one source of truth for the agent schema/registry/documents, shared with the
  telephony pipeline.
* Drops the standalone builder's subprocess Runtime-Control and Sarvam key-pool
  routes: the ops dashboard already owns runtime control + the key pool, so we
  avoid /api/runtime/* and key-route collisions.
* `activate` writes agents/active_agent.json — the single-active-agent selector
  that conversation_call_bridge.py reads at call time. That file IS the wire
  between "build an agent in the dashboard" and "telephony runs that agent".
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import struct
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response

logger = logging.getLogger("agent_builder_api")

# ── Make the calling-agent backend importable from the dashboard process ────
_REPO_ROOT = Path(__file__).resolve().parents[1]          # /home/angelo/lescall
_CALLING_AGENT = _REPO_ROOT / "calling-agent"
if str(_CALLING_AGENT) not in sys.path:
    sys.path.insert(0, str(_CALLING_AGENT))

# Load calling-agent/.env so the builder's RAG (OpenAI embeddings / yaml_filler)
# and TTS preview (Sarvam) have their keys in os.environ even though the existing
# dashboard reads keys straight from the .env file.
try:
    from dotenv import load_dotenv
    load_dotenv(_CALLING_AGENT / ".env")
except Exception:  # pragma: no cover
    pass

from backend.app.agent_definition.schema import AgentDefinition   # noqa: E402
from backend.app.agent_definition.registry import agent_registry  # noqa: E402

_AGENTS_DIR = _CALLING_AGENT / "agents"
_ACTIVE_FILE = _AGENTS_DIR / "active_agent.json"


# ── multi-tenant boundary ────────────────────────────────────────────────────
# Every /api/agents route runs _tenancy_dep (router-level dependency): it
# resolves the caller's owner email, stashes it in a ContextVar (so the
# Copilot's in-process calls to these handlers inherit the same tenant), and
# 404s any {name} the caller doesn't own. Admins (auth.admin_emails) see all.
# Unclaimed slugs (no agents-index row) are hidden from tenants — creation
# claims the slug for its creator.
from contextvars import ContextVar  # noqa: E402

_REQ_OWNER: ContextVar[str | None] = ContextVar("lescall_req_owner", default=None)


def _ctx_owner() -> str:
    """Owner for the current request context (builder or copilot)."""
    owner = _REQ_OWNER.get()
    if owner:
        return owner
    import auth
    return auth.DEFAULT_OWNER  # non-HTTP/internal callers act as the admin


def _ctx_is_admin() -> bool:
    import auth
    return auth.is_admin(_ctx_owner())


def _slug_owner(slug: str) -> str | None:
    """agents-index owner for a slug; None if no row. Fails CLOSED: a DB error
    raises, it never silently grants access."""
    from shared.db.database import get_db
    with get_db() as db:
        row = db.execute("SELECT owner FROM agents WHERE slug=?", (slug,)).fetchone()
        return (row["owner"] or None) if row else None


def _claim_slug(slug: str, owner: str, display_name: str = "") -> None:
    from shared.db.database import get_db
    with get_db() as db:
        db.execute(
            "INSERT INTO agents (slug, owner, display_name) VALUES (?,?,?) "
            "ON CONFLICT (slug) DO NOTHING", (slug, owner, display_name or None))
        db.execute("UPDATE agents SET owner=? WHERE slug=? AND owner IS NULL",
                   (owner, slug))


def _visible_slugs(owner: str) -> set[str] | None:
    """Slugs this owner may see; None = unrestricted (admin)."""
    import auth
    if auth.is_admin(owner):
        return None
    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute("SELECT slug FROM agents WHERE owner=?", (owner,)).fetchall()
    return {r["slug"] for r in rows}


def _assert_slug_access(slug: str, owner: str) -> None:
    import auth
    if auth.is_admin(owner):
        return
    if _slug_owner(slug) != owner:
        # 404 (not 403): don't confirm a foreign agent's existence
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")


async def _tenancy_dep(request: Request):
    """MUST stay async: a sync dependency runs in a threadpool and its
    ContextVar assignment would not reach the handler's task."""
    import auth
    owner = auth.current_owner(request)  # 401 when OAuth is on and no session
    _REQ_OWNER.set(owner)
    request.state.owner = owner
    name = request.path_params.get("name")
    if name:
        _assert_slug_access(name, owner)


router = APIRouter(dependencies=[Depends(_tenancy_dep)])


# ── helpers ─────────────────────────────────────────────────────────────────
def _read_active() -> str | None:
    try:
        from shared.db.database import get_db
        with get_db() as db:
            row = db.execute(
                "SELECT slug FROM agents WHERE is_active=1 "
                "ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and row["slug"]:
            return row["slug"]
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("[builder] DB active-agent lookup failed: %r", exc)
    try:  # legacy fallback while active_agent.json is still dual-written
        if _ACTIVE_FILE.exists():
            data = json.loads(_ACTIVE_FILE.read_text(encoding="utf-8")) or {}
            return data.get("active")
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("[builder] could not read active_agent.json: %r", exc)
    return None


def _write_active(slug: str | None) -> None:
    """Single-active-agent selector: exactly one agents row has is_active=1
    (or none, when slug is None). Dual-writes the legacy JSON until every
    reader has soaked on the DB path."""
    from shared.db.database import get_db
    with get_db() as db:
        db.execute("UPDATE agents SET is_active=0 WHERE is_active=1")
        if slug:
            db.execute(
                "UPDATE agents SET is_active=1, updated_at=CURRENT_TIMESTAMP "
                "WHERE slug=?", (slug,))
    try:
        if slug:
            _ACTIVE_FILE.write_text(json.dumps({"active": slug}, indent=2),
                                    encoding="utf-8")
        elif _ACTIVE_FILE.exists():
            _ACTIVE_FILE.unlink()
    except Exception as exc:  # pragma: no cover - legacy mirror only
        logger.warning("[builder] could not mirror active_agent.json: %r", exc)


def _agent_summary(name: str, agent: AgentDefinition) -> dict:
    return {
        "name": name,
        "agent_id": agent.agent_id,
        "version": agent.version,
        "agent_name": agent.identity.agent_name,
        "company": agent.identity.company_name,
        "languages": agent.languages.supported,
        "locations": agent.knowledge_base.locations,
        "stages": [s.id for s in agent.call_flow.stages],
        "call_direction": getattr(agent, "call_direction", "inbound"),
        "calendar_slots": len(agent.calendar.mock_slots or []),
    }


# ── list / read ─────────────────────────────────────────────────────────────
@router.get("/api/agents")
async def list_agents() -> dict:
    """List the caller's agent definitions, plus which is globally active.

    Tenants see only slugs they own (agents index); admins see everything.
    The global-active slug is admin infrastructure — hidden from tenants.
    """
    visible = _visible_slugs(_ctx_owner())
    is_admin = visible is None
    active = _read_active() if is_admin else None
    result = []
    for yaml_path in sorted(_AGENTS_DIR.glob("*/agent.yaml")):
        name = yaml_path.parent.name
        if visible is not None and name not in visible:
            continue
        try:
            agent = AgentDefinition.from_yaml(yaml_path)
            summary = _agent_summary(name, agent)
            summary["active"] = (name == active)
            result.append(summary)
        except Exception as exc:
            result.append({"name": name, "error": str(exc), "active": name == active})
    return {"agents": result, "active": active}


@router.get("/api/agents/active")
async def get_active_agent() -> dict:
    """Slug of the global-fallback agent (admin infrastructure; null for tenants)."""
    return {"active": _read_active() if _ctx_is_admin() else None}


@router.get("/api/agents/{name}")
async def get_agent(name: str) -> dict:
    """Full configuration of a named agent (the editor reads this).

    `_rev` is the optimistic-concurrency token: send it back as If-Match on
    PUT so an interleaved edit 409s instead of being silently clobbered."""
    yaml_path = _AGENTS_DIR / name / "agent.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    try:
        from backend.app.agent_definition import store as agent_store
        _, rev = agent_store.read_agent(name, _AGENTS_DIR)
        agent = AgentDefinition.from_yaml(yaml_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {**dataclasses.asdict(agent), "_rev": rev}


# ── create / update / archive ───────────────────────────────────────────────
@router.post("/api/agents")
async def create_agent(request: Request) -> dict:
    """Create a new agent. Body: {"name": "<slug>", ...agent_definition...}."""
    body = await request.json()
    name = body.pop("name", None)
    if not name:
        raise HTTPException(status_code=400, detail="'name' field is required")
    if not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="'name' must be alphanumeric with _ or -")
    target_dir = _AGENTS_DIR / name
    if target_dir.exists():
        raise HTTPException(status_code=409, detail=f"Agent '{name}' already exists — use PUT to update")
    # Never write into a slug the DB already assigns to another tenant (guards
    # the narrow orphaned-row case where the folder was removed but the owner
    # row survived).
    _existing_owner = _slug_owner(name)
    if _existing_owner and _existing_owner != _ctx_owner() and not _ctx_is_admin():
        raise HTTPException(status_code=409, detail=f"Agent '{name}' already exists")
    try:
        agent = AgentDefinition.from_dict(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Schema validation failed: {exc}")
    target_dir.mkdir(parents=True, exist_ok=True)
    agent.to_yaml(target_dir / "agent.yaml")
    agent_registry.put(name, agent)
    # Multi-tenant: creation claims the slug for its creator (agents index).
    owner = _ctx_owner()
    try:
        _claim_slug(name, owner, agent.identity.agent_name)
    except Exception as exc:
        # Fail CLOSED: an unclaimed agent would be invisible/unusable for a
        # tenant and grabbable later — undo the create instead.
        if not _ctx_is_admin():
            shutil.rmtree(target_dir, ignore_errors=True)
            agent_registry.invalidate(name)
            raise HTTPException(status_code=500,
                                detail=f"could not register ownership: {exc}")
        logger.warning("[builder] ownership claim failed for %s: %r", name, exc)
    logger.info("[builder] created agent '%s' (owner=%s)", name, owner)
    return {"status": "created", "name": name, "agent_id": agent.agent_id}


@router.put("/api/agents/{name}")
async def update_agent(name: str, request: Request) -> dict:
    """Replace an existing agent's YAML (full agent definition body).

    Routes through the store choke point: revision snapshot + audit stamps +
    atomic write. Optional If-Match (the `_rev` from GET) → 409 on a
    concurrent edit; absent = legacy last-write-wins (old clients keep working)."""
    yaml_path = _AGENTS_DIR / name / "agent.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found — POST to create")
    body = await request.json()
    body.pop("name", None)
    body.pop("_rev", None)
    base_rev = request.headers.get("If-Match") or None
    from backend.app.agent_definition import store as agent_store
    try:
        result = agent_store.write_agent(
            name, body, actor=f"{_ctx_owner()}:builder", base_rev=base_rev,
            reason="builder full update", owner=_ctx_owner(),
            is_admin=_ctx_is_admin(), agents_dir=_AGENTS_DIR)
    except agent_store.ConflictError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "conflict",
            "message": "This agent changed since you loaded it — reload and reapply.",
            "current_rev": exc.current_rev})
    except agent_store.OwnerMismatch as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Schema validation failed: {exc}")
    logger.info("[builder] updated agent '%s' rev=%s", name, result["revision"])
    return {"status": "updated", "name": name, "revision": result["revision"],
            "rev": result["rev"]}


@router.delete("/api/agents/{name}")
async def archive_agent(name: str) -> dict:
    """Archive an agent (move to agents/_archived/). Not a hard delete."""
    agent_dir = _AGENTS_DIR / name
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    archived_base = _AGENTS_DIR / "_archived"
    archived_base.mkdir(exist_ok=True)
    dest = archived_base / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(agent_dir), str(dest))
    agent_registry.invalidate(name)
    # If the archived agent was active, clear the selection.
    if _read_active() == name:
        try:
            _write_active(None)
        except Exception:
            pass
    logger.info("[builder] archived agent '%s'", name)
    return {"status": "archived", "name": name, "archived_to": str(dest)}


@router.post("/api/agents/{name}/validate")
async def validate_agent(name: str, request: Request) -> dict:
    """Dry-run schema validation. Accepts raw YAML (text/*) or JSON body."""
    content_type = request.headers.get("content-type", "")
    try:
        if "text/" in content_type or "yaml" in content_type:
            raw = (await request.body()).decode("utf-8")
            body = yaml.safe_load(raw)
        else:
            body = await request.json()
            body.pop("name", None)
        agent = AgentDefinition.from_dict(body)
        return {
            "valid": True,
            "agent_id": agent.agent_id,
            "version": agent.version,
            "stages": [s.id for s in agent.call_flow.stages],
            "languages": agent.languages.supported,
        }
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


# ── raw YAML ────────────────────────────────────────────────────────────────
@router.get("/api/agents/{name}/raw")
async def get_agent_raw(name: str) -> PlainTextResponse:
    yaml_path = _AGENTS_DIR / name / "agent.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return PlainTextResponse(yaml_path.read_text(encoding="utf-8"))


@router.put("/api/agents/{name}/raw")
async def update_agent_raw(name: str, request: Request) -> dict:
    yaml_path = _AGENTS_DIR / name / "agent.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found — POST to create")
    text = (await request.body()).decode("utf-8")
    try:
        data = yaml.safe_load(text)
        AgentDefinition.from_dict(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"YAML parse/validation failed: {exc}")
    base_rev = request.headers.get("If-Match") or None
    from backend.app.agent_definition import store as agent_store
    try:
        result = agent_store.write_agent(
            name, data, actor=f"{_ctx_owner()}:builder", base_rev=base_rev,
            reason="builder raw YAML update", owner=_ctx_owner(),
            is_admin=_ctx_is_admin(), agents_dir=_AGENTS_DIR)
    except agent_store.ConflictError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "conflict",
            "message": "This agent changed since you loaded it — reload and reapply.",
            "current_rev": exc.current_rev})
    except agent_store.OwnerMismatch as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    logger.info("[builder] raw-updated agent '%s' rev=%s", name, result["revision"])
    return {"status": "updated", "name": name, "revision": result["revision"],
            "rev": result["rev"]}


@router.get("/api/agents/{name}/revisions")
async def agent_revisions(name: str) -> dict:
    """Edit history (agent_revisions snapshots), newest first."""
    if not (_AGENTS_DIR / name / "agent.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    from backend.app.agent_definition import store as agent_store
    revs = agent_store.list_revisions(name)
    return {"revisions": [
        {"rev_id": r["id"], "actor": r["actor"], "reason": r["reason"],
         "sha256": r["sha256"], "created_at": str(r["created_at"])}
        for r in revs]}


@router.post("/api/agents/{name}/rollback/{rev_id}")
async def agent_rollback(name: str, rev_id: int) -> dict:
    """Restore a snapshot (itself snapshotted — rollback is undoable)."""
    from backend.app.agent_definition import store as agent_store
    try:
        result = agent_store.rollback_agent(
            name, rev_id, actor=f"{_ctx_owner()}:rollback",
            owner=_ctx_owner(), is_admin=_ctx_is_admin(), agents_dir=_AGENTS_DIR)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except agent_store.OwnerMismatch as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "rolled_back", "name": name, "rev_id": rev_id,
            "revision": result["revision"], "effective": "next_call"}


@router.patch("/api/agents/{name}/ops")
async def agent_ops(name: str, request: Request) -> dict:
    """Surgical path operations (the same machinery the Copilot uses):
    body {ops: [{op: set|append|remove_at|move, path, value?, to?}], base_rev?}."""
    yaml_path = _AGENTS_DIR / name / "agent.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    body = await request.json()
    ops = body.get("ops") or []
    if not isinstance(ops, list) or not ops:
        raise HTTPException(status_code=400, detail="'ops' (non-empty list) is required")
    from backend.app.agent_definition import store as agent_store
    from backend.app.agent_definition.diff import apply_ops, compute_diff, render_human
    current, cur_rev = agent_store.read_agent(name, _AGENTS_DIR)
    base_rev = body.get("base_rev") or request.headers.get("If-Match") or cur_rev
    try:
        candidate = apply_ops(current, ops)
        AgentDefinition.from_dict(candidate)
        expected = {str(o.get("path") or "").split("[")[0].rstrip(".")
                    for o in ops if o.get("path")}
        result = agent_store.write_agent(
            name, candidate, actor=f"{_ctx_owner()}:builder", base_rev=base_rev,
            reason="builder ops: " + ", ".join(str(o.get("op")) for o in ops)[:120],
            owner=_ctx_owner(), is_admin=_ctx_is_admin(),
            expected_paths=expected, agents_dir=_AGENTS_DIR)
    except agent_store.ConflictError as exc:
        raise HTTPException(status_code=409, detail={
            "error": "conflict",
            "message": "This agent changed since you loaded it — reload and reapply.",
            "current_rev": exc.current_rev})
    except agent_store.ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    diff = compute_diff(current, candidate)
    return {"status": "updated", "name": name, "revision": result["revision"],
            "rev": result["rev"], "diff": render_human(diff),
            "effective": "next_call"}


# ── routing.yaml ────────────────────────────────────────────────────────────
@router.get("/api/agents/{name}/routing")
async def get_routing(name: str) -> dict:
    routing_path = _AGENTS_DIR / name / "routing.yaml"
    if not routing_path.exists():
        return {"exists": False, "did": "", "caller_id_prefixes": [], "time_windows": [], "fallback_agent": ""}
    data = yaml.safe_load(routing_path.read_text(encoding="utf-8")) or {}
    return {"exists": True, **data}


@router.put("/api/agents/{name}/routing")
async def update_routing(name: str, request: Request) -> dict:
    if not (_AGENTS_DIR / name).exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    routing_path = _AGENTS_DIR / name / "routing.yaml"
    body = await request.json()
    with open(routing_path, "w", encoding="utf-8") as f:
        yaml.dump(body, f, allow_unicode=True, default_flow_style=False)
    logger.info("[builder] updated routing for agent '%s'", name)
    return {"status": "updated"}


# ── stateless document extraction (Create-agent flow) ──────────────────────
@router.post("/api/agent-tools/extract-document")
async def extract_from_document(file: UploadFile = File(...)) -> dict:
    """Extract agent fields from an uploaded document WITHOUT needing an
    existing agent — used by the Create wizard to prefill the form. Returns
    {"fields": {...}, "missing": [{key,label,hint}...]} so the UI can ask the
    user for everything the document lacked."""
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        from backend.app.documents.extractor import extract_text
        extracted = extract_text(content, file.filename or "upload.txt")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Text extraction failed: {exc}")
    if not extracted.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from this file")
    from backend.app.documents.yaml_filler import extract_fields_from_text
    result = extract_fields_from_text(extracted)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.get("error", "Extraction failed"))
    result["filename"] = file.filename
    result["char_count"] = len(extracted)
    return result


# ── calendar bookings ───────────────────────────────────────────────────────
@router.get("/api/agents/{name}/bookings")
async def list_agent_bookings(name: str) -> dict:
    """Booked appointment slots recorded by live calls (bookings.json)."""
    if not (_AGENTS_DIR / name).exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    from backend.app.agent_definition.calendar_store import list_bookings
    return {"bookings": list_bookings(name)}


@router.delete("/api/agents/{name}/bookings/{index}")
async def delete_agent_booking(name: str, index: int) -> dict:
    """Free a booked slot again (e.g. cancelled appointment)."""
    from backend.app.agent_definition.calendar_store import delete_booking
    if not delete_booking(name, index):
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"status": "deleted", "index": index}


# ── documents + RAG ─────────────────────────────────────────────────────────
@router.post("/api/agents/{name}/documents")
async def upload_document(
    name: str,
    file: UploadFile = File(...),
    org_name: str = Form(""),
    doc_type: str = Form("General"),
    description: str = Form(""),
) -> dict:
    """Upload a document: extract text, chunk, embed (for in-call RAG)."""
    if not (_AGENTS_DIR / name).exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        from backend.app.documents.extractor import extract_text
        extracted = extract_text(content, file.filename or "upload.txt")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Text extraction failed: {exc}")
    if not extracted.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from this file")
    from backend.app.documents.store import save_document
    meta = save_document(
        agent_folder=name,
        filename=file.filename or "document.txt",
        content=content,
        extracted_text=extracted,
        org_name=org_name,
        doc_type=doc_type,
        description=description,
    )
    try:
        from backend.app.documents.embeddings import embed_document
        embed_document(name, meta["doc_id"])
    except Exception as exc:
        logger.warning("[builder/docs] embedding failed for %s/%s: %s", name, meta["doc_id"], exc)
    return {"status": "uploaded", "doc_id": meta["doc_id"], "meta": meta}


@router.get("/api/agents/{name}/documents")
async def list_agent_documents(name: str) -> dict:
    index_path = _AGENTS_DIR / name / "documents" / "index.json"
    if not index_path.exists():
        return {"documents": []}
    try:
        return {"documents": json.loads(index_path.read_text(encoding="utf-8"))}
    except Exception as exc:
        logger.warning("[builder/docs] could not read index for '%s': %s", name, exc)
        return {"documents": []}


@router.delete("/api/agents/{name}/documents/{doc_id}")
async def delete_agent_document(name: str, doc_id: str) -> dict:
    from backend.app.documents.store import delete_document
    if not delete_document(name, doc_id):
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return {"status": "deleted", "doc_id": doc_id}


@router.post("/api/agents/{name}/documents/{doc_id}/extract-yaml")
async def extract_yaml_from_document(name: str, doc_id: str) -> dict:
    """LLM-extract agent fields from a document; returns a diff preview (no write)."""
    from backend.app.documents.store import get_document_text
    text = get_document_text(name, doc_id)
    if not text:
        raise HTTPException(status_code=404, detail="Document text not found")
    from backend.app.documents.yaml_filler import extract_fields_from_text
    result = extract_fields_from_text(text)
    result["agent_folder"] = name
    result["doc_id"] = doc_id
    return result


@router.post("/api/agents/{name}/documents/{doc_id}/apply-yaml")
async def apply_yaml_from_document(name: str, doc_id: str, request: Request) -> dict:
    """Apply extracted fields (from the diff preview) into the agent's YAML."""
    body = await request.json()
    fields = body.get("fields", {})
    if not fields:
        raise HTTPException(status_code=400, detail="No fields provided to apply")
    from backend.app.documents.yaml_filler import apply_fields
    result = apply_fields(name, fields)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error", "Apply failed"))
    agent_registry.invalidate(name)
    return result


# ── TTS voice preview ───────────────────────────────────────────────────────
@router.get("/api/tts/preview")
async def tts_voice_preview(speaker: str, model: str = "bulbul:v3", language: str = "en-IN"):
    """Stream a short TTS sample as audio/wav for in-browser playback."""
    import httpx
    api_key = os.environ.get("SARVAM_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="SARVAM_API_KEY not configured")
    preview_text = {
        "en-IN": "Hi there! I'm your AI assistant. How can I help you today?",
        "hi-IN": "नमस्ते! मैं आपकी AI सहायक हूँ। आज मैं आपकी कैसे मदद कर सकती हूँ?",
        "gu-IN": "નમસ્તે! હું તમારી AI સહાયક છું. આજે હું તમને કેવી રીતે મદદ કરી શકું?",
    }
    text = preview_text.get(language, preview_text["en-IN"])
    payload = {
        "text": text,
        "target_language_code": language,
        "speaker": speaker,
        "model": model,
        "speech_sample_rate": 16000,
        "output_audio_codec": "linear16",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            async with client.stream(
                "POST",
                "https://api.sarvam.ai/text-to-speech/stream",
                headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise HTTPException(status_code=resp.status_code, detail=body.decode(errors="replace"))
                pcm = b"".join([chunk async for chunk in resp.aiter_bytes(4096)])
        sr, ch, bps = 16000, 1, 16
        wav = struct.pack("<4sI4s", b"RIFF", 36 + len(pcm), b"WAVE")
        wav += struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, ch, sr, sr * ch * bps // 8, ch * bps // 8, bps)
        wav += struct.pack("<4sI", b"data", len(pcm)) + pcm
        return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── intent preview (no LLM) ─────────────────────────────────────────────────
@router.post("/api/preview/intent")
async def preview_intent(request: Request) -> dict:
    """Pattern-match caller text against an agent's deterministic intent rules."""
    body = await request.json()
    text = body.get("text", "").strip()
    language = body.get("language", "auto")

    req_agent = (body.get("agent") or "").strip()
    agent_name = req_agent or (_read_active() if _ctx_is_admin() else "")
    if not agent_name:
        # Default to one of the CALLER's own agents, never another tenant's.
        visible = _visible_slugs(_ctx_owner())
        yamls = [p for p in sorted(_AGENTS_DIR.glob("*/agent.yaml"))
                 if visible is None or p.parent.name in visible]
        if not yamls:
            raise HTTPException(status_code=503, detail="No agents found — create one first")
        agent_name = yamls[0].parent.name
    # Owner boundary: this route has no {name} path param so the router
    # dependency never ran _assert_slug_access — enforce it explicitly.
    _assert_slug_access(agent_name, _ctx_owner())
    try:
        agent_def = agent_registry.get(agent_name, _AGENTS_DIR)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found: {exc}")

    kb = agent_def.knowledge_base
    text_lower = text.lower()
    matches: list[dict] = []
    for pattern in kb.objection_patterns:
        if pattern.lower() in text_lower:
            matches.append({"intent": "objection", "pattern": pattern})
    for pattern in kb.time_patterns:
        if pattern.lower() in text_lower:
            matches.append({"intent": "time_request", "pattern": pattern})
    for alias in kb.location_aliases:
        if alias.lower() in text_lower:
            matches.append({"intent": "location_selection", "pattern": alias})
    qa_hits: list[dict] = []
    for field_name, signals in kb.qa_field_signals.items():
        for sig in signals:
            if sig.lower() in text_lower:
                qa_hits.append({"field": field_name, "signal": sig})
    top_intent = matches[0]["intent"] if matches else ("qa_signal" if qa_hits else "none")
    return {
        "text": text,
        "language": language,
        "intent": top_intent,
        "matches": matches,
        "qa_signals": qa_hits,
        "agent_id": agent_def.agent_id,
        "patterns_checked": {
            "objection": len(kb.objection_patterns),
            "time": len(kb.time_patterns),
            "location": len(kb.location_aliases),
            "qa_fields": len(kb.qa_field_signals),
        },
    }


# ── activate (THE wire to telephony) ────────────────────────────────────────
@router.post("/api/agents/{name}/activate")
async def activate_agent(name: str) -> dict:
    """Make this agent answer inbound calls.

    Writes agents/active_agent.json — the single-active-agent selector that
    conversation_call_bridge.py reads when each call starts. The next call will
    run this agent; calls already in progress are unaffected.

    ADMIN ONLY: this is the platform-wide FALLBACK for calls that don't match
    a DID binding — letting a tenant flip it would re-route other tenants'
    calls. Tenants route their own numbers via the Telephony panel instead.
    """
    if not _ctx_is_admin():
        raise HTTPException(
            status_code=403,
            detail="Activation is platform-admin only — bind your number to "
                   "this agent in the Telephony panel instead")
    yaml_path = _AGENTS_DIR / name / "agent.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    try:
        agent = AgentDefinition.from_yaml(yaml_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to load agent: {exc}")
    _write_active(name)
    agent_registry.invalidate(name)
    logger.info("[builder] activated agent '%s' (%s) for inbound calls", name, agent.agent_id)
    return {
        "status": "activated",
        "name": name,
        "agent_id": agent.agent_id,
        "agent_name": agent.identity.agent_name,
        "note": "Next inbound call will use this agent.",
    }
