"""Share an agent by link; the recipient imports an INDEPENDENT copy.

The one property everything here defends: an imported agent has no
relationship to its source. New slug, new agent_id, own history, own owner —
and edits on either side stay put. The rest of the file is the ways that can
quietly stop being true: a shared agent_id silently redirecting another
tenant's document lookups, half an imported twin pair surviving a failure, a
double-clicked Import consuming two plan slots, a revoked link still working.

Two tiers:
  · pure — slug/id/spec algebra and the login-redirect guard. No DB, no I/O.
  · DB   — the full create → preview → import path against lescall_test
           (pg_test_db), with agents/ redirected at a tmp tree.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
for p in (str(_REPO / "dashboard"), str(_REPO), str(_REPO / "calling-agent")):
    if p not in sys.path:
        sys.path.insert(0, p)

import agent_builder_api as builder  # noqa: E402
import agent_share_api as share  # noqa: E402

def _run(coro):
    """The repo's convention for exercising an async handler from a sync test
    (see test_copilot_edit_engine.py) — pytest-asyncio is not installed."""
    import asyncio
    return asyncio.run(coro)



# ── the definition every test copies around ─────────────────────────────────

def _spec(direction: str = "inbound", name: str = "Asha") -> dict:
    return {
        "agent_id": "acme-v1",
        "version": "1.0.0",
        "call_direction": direction,
        "revision": 47,
        "updated_at": "2026-01-01T00:00:00Z",
        "updated_by": "someone@else.com",
        "identity": {"agent_name": name, "company_name": "Acme Homes"},
        "languages": {"supported": ["en"], "default": "en",
                      "tts_voices": {"en": {"model": "bulbul:v3",
                                            "speaker": "shubh"}}},
        "call_flow": {"stages": [{"id": "GREETING", "order": 0, "goal": "hello"},
                                 {"id": "CLOSING", "order": 1, "goal": "bye"}]},
        "response_bank": {"opening_greeting": "Hello"},
        "knowledge_base": {"locations": ["Surat"]},
    }


# ══════════════════════════════════════════════════════ pure ════════════════

def test_sanitize_family_never_yields_an_empty_or_unsafe_slug():
    assert share._sanitize_family("Acme  Homes!! Pvt") == "acme-homes-pvt"
    assert share._sanitize_family("../../etc/passwd") == "etc-passwd"
    # A name that sanitizes away entirely must still produce a usable folder
    # name — "" would write the import into the agents/ root.
    assert share._sanitize_family("---") == "agent"
    assert share._sanitize_family("") == "agent"


def test_prepare_spec_strips_history_and_remints_identity(tmp_path):
    src = tmp_path / "agent.yaml"
    src.write_text(yaml.safe_dump(_spec()), encoding="utf-8")

    out = share._prepare_spec(src, "newco-inbound", set())

    # History belongs to the SOURCE record. A copy claiming revision 47 and
    # "updated_by someone@else.com" makes the importer's first real edit look
    # like a rollback.
    assert out["revision"] == 1
    assert "updated_at" not in out
    assert "updated_by" not in out
    assert out["agent_id"] == "newco-inbound-v1"
    # Everything that describes BEHAVIOUR travels untouched.
    assert out["identity"]["agent_name"] == "Asha"
    assert out["call_flow"]["stages"][0]["goal"] == "hello"


def test_agent_id_is_reminted_away_from_a_collision(tmp_path):
    """documents/store.get_folder_by_agent_id() scans agent.yaml files and
    returns the FIRST agent_id match, so two agents sharing an id means one
    tenant's RAG lookups can resolve into another tenant's document folder."""
    src = tmp_path / "agent.yaml"
    src.write_text(yaml.safe_dump(_spec()), encoding="utf-8")

    taken = {"newco-inbound-v1"}
    out = share._prepare_spec(src, "newco-inbound", taken)
    assert out["agent_id"] != "newco-inbound-v1"
    # …and the caller's set is updated, so a second variant in the same import
    # cannot collide with the first one either.
    assert out["agent_id"] in taken


def test_prepare_spec_rejects_a_definition_that_is_not_a_mapping(tmp_path):
    src = tmp_path / "agent.yaml"
    src.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(Exception):
        share._prepare_spec(src, "newco", set())


@pytest.mark.parametrize("path", [
    "//evil.example",            # protocol-relative: a browser treats it as a host
    "https://evil.example",
    "/\\evil.example",
    "/x\\y",
    "/login",                    # would bounce forever
    "/login?next=/x",
    "/auth/callback",
    "",
    "/" + "a" * 600,             # absurd length
    # Browser-initiated asset fetches: their bounce off the login wall used to
    # overwrite the stashed destination, sending fresh logins to /favicon.ico.
    "/favicon.ico",
    "/logo.png",
    "/assets/index-Ck2fA9.js",
])
def test_login_return_path_refuses_open_redirects(path):
    import auth
    assert auth.safe_return_path(path) == ""


@pytest.mark.parametrize("path", [
    "/s/abc123",
    "/build/agents?import=abc123",
    "/contacts/42",
])
def test_login_return_path_accepts_same_origin_deep_links(path):
    import auth
    assert auth.safe_return_path(path) == path


# ══════════════════════════════════════════════════════ DB ══════════════════

from tests.test_intel_queue import pg_test_db  # noqa: E402,F401  (fixture)

SHARER = "sharer@example.com"
TAKER = "taker@example.com"


@pytest.fixture(autouse=True)
def _isolated(pg_test_db, monkeypatch, tmp_path):
    """Point the DB at lescall_test and agents/ at a scratch tree.

    Without the second half these tests would write real agent folders into
    the repo and hand them ownership rows in whatever database happened to be
    configured.
    """
    monkeypatch.setenv("LESCALL_DB_URL", pg_test_db)
    agents = tmp_path / "agents"
    agents.mkdir()
    monkeypatch.setattr(builder, "_AGENTS_DIR", agents)
    monkeypatch.setattr(share, "_AGENTS_DIR", agents)
    monkeypatch.setattr(share, "_ARCHIVE_DIR", agents / "_archived")
    monkeypatch.setattr(share, "_TABLES_READY", True)
    from shared.db.database import get_db
    with get_db() as db:
        db.execute("DELETE FROM agent_share_imports")
        db.execute("DELETE FROM agent_shares")
        db.execute("DELETE FROM agents")
    yield agents


def _seed_family(agents_dir: Path, family: str, owner: str,
                 directions=("inbound", "outbound")) -> list[str]:
    """Write a family to disk and claim it, the way a real create would."""
    slugs = []
    for d in directions:
        slug = f"{family}-{d}" if d else family
        (agents_dir / slug).mkdir(parents=True)
        (agents_dir / slug / "agent.yaml").write_text(
            yaml.safe_dump(_spec(d or "inbound")), encoding="utf-8")
        from shared.db.database import get_db
        with get_db() as db:
            db.execute("INSERT INTO agents (slug, owner, display_name) "
                       "VALUES (?,?,?)", (slug, owner, "Asha"))
        slugs.append(slug)
    return slugs


class _Req:
    """Minimal stand-in for the parts of Request these handlers touch."""

    def __init__(self, owner: str, body: dict | None = None):
        self.state = type("S", (), {"owner": owner})()
        self._body = body or {}
        self.base_url = "https://agent.lescall.com/"
        self.client = None

    async def json(self):
        return self._body


def _create_link(family_slug: str, owner: str) -> dict:
    res = _run(share.create_link(_Req(owner, {"slug": family_slug})))
    return res["link"]


# ── the sharer's end ────────────────────────────────────────────────────────

def test_create_link_is_idempotent(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    first = _create_link("acme-inbound", SHARER)
    second = _create_link("acme-outbound", SHARER)
    # Same FAMILY, so the same link — otherwise revoking "the" link would
    # leave a second one live that the user never knew they made.
    assert first["token"] == second["token"]
    assert first["url"].endswith(f"/s/{first['token']}")


def test_cannot_share_an_agent_you_do_not_own(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _create_link("acme-inbound", TAKER)
    # 404, not 403: never confirm that another tenant's agent exists.
    assert exc.value.status_code == 404


# ── the recipient's end ─────────────────────────────────────────────────────

def test_import_creates_an_independent_copy(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)

    result = _run(share.import_share(link["token"], _Req(TAKER)))

    assert result["family"] != "acme"
    assert len(result["slugs"]) == 2          # both directions travel together
    for slug in result["slugs"]:
        assert (_isolated / slug / "agent.yaml").exists()
        assert builder._slug_owner(slug) == TAKER

    # THE property: editing the source does not touch the copy.
    src = _isolated / "acme-inbound" / "agent.yaml"
    edited = yaml.safe_load(src.read_text(encoding="utf-8"))
    edited["response_bank"]["opening_greeting"] = "CHANGED BY THE SHARER"
    src.write_text(yaml.safe_dump(edited), encoding="utf-8")

    copy = yaml.safe_load(
        (_isolated / result["slugs"][0] / "agent.yaml").read_text(encoding="utf-8"))
    assert copy["response_bank"]["opening_greeting"] == "Hello"
    # …and the two records do not share an identity.
    assert copy["agent_id"] != "acme-v1"


def test_imported_agent_is_never_the_accounts_active_fallback(_isolated):
    """agents.is_active defaults to 1 and the bridge picks the most recently
    updated is_active row as the owner's unbound-call agent. Importing an
    agent to look at it must not take over answering their calls."""
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    result = _run(share.import_share(link["token"], _Req(TAKER)))

    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute(
            "SELECT slug, is_active FROM agents WHERE owner=?", (TAKER,)).fetchall()
    assert rows and all(not r["is_active"] for r in rows)
    assert {r["slug"] for r in rows} == set(result["slugs"])


def test_documents_travel_with_the_agent(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    docs = _isolated / "acme-inbound" / "documents" / "d1"
    docs.mkdir(parents=True)
    (docs / "text.txt").write_text("brochure", encoding="utf-8")
    (docs / "chunks.json").write_text('["brochure"]', encoding="utf-8")
    (docs.parent / "index.json").write_text(
        json.dumps([{"doc_id": "d1", "filename": "b.pdf"}]), encoding="utf-8")

    link = _create_link("acme-inbound", SHARER)
    result = _run(share.import_share(link["token"], _Req(TAKER)))

    inbound = next(s for s in result["slugs"] if s.endswith("-inbound"))
    copied = _isolated / inbound / "documents" / "d1" / "text.txt"
    assert copied.read_text(encoding="utf-8") == "brochure"


def test_routing_and_bookings_do_not_travel(_isolated):
    """routing.yaml is the sharer's DID binding and bookings.json is their
    customers' appointments. Copying either hands the importer's brand-new
    agent someone else's phone number and someone else's calendar."""
    _seed_family(_isolated, "acme", SHARER)
    (_isolated / "acme-inbound" / "routing.yaml").write_text(
        "did: '+918065953160'\n", encoding="utf-8")
    (_isolated / "acme-inbound" / "bookings.json").write_text(
        '[{"caller_phone": "+919999999999"}]', encoding="utf-8")

    link = _create_link("acme-inbound", SHARER)
    result = _run(share.import_share(link["token"], _Req(TAKER)))

    inbound = next(s for s in result["slugs"] if s.endswith("-inbound"))
    assert not (_isolated / inbound / "routing.yaml").exists()
    assert not (_isolated / inbound / "bookings.json").exists()


def test_importing_your_own_link_is_refused(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run(share.import_share(link["token"], _Req(SHARER)))
    assert exc.value.status_code == 409


def test_importing_twice_does_not_consume_two_slots(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    first = _run(share.import_share(link["token"], _Req(TAKER)))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run(share.import_share(link["token"], _Req(TAKER)))
    assert exc.value.status_code == 409

    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute("SELECT slug FROM agents WHERE owner=?", (TAKER,)).fetchall()
    assert {r["slug"] for r in rows} == set(first["slugs"])


def test_reimport_is_allowed_once_the_earlier_copy_is_archived(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    first = _run(share.import_share(link["token"], _Req(TAKER)))

    # Archive it the way DELETE /api/agents/{name} does: folder moved out,
    # row kept but stamped.
    from shared.db.database import get_db
    for slug in first["slugs"]:
        shutil.rmtree(_isolated / slug)
        with get_db() as db:
            db.execute("UPDATE agents SET archived_at=CURRENT_TIMESTAMP "
                       "WHERE slug=?", (slug,))

    again = _run(share.import_share(link["token"], _Req(TAKER)))
    assert set(again["slugs"]) != set(first["slugs"])   # fresh, unused slugs


def test_revoked_link_is_gone_not_merely_missing(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    _run(share.revoke_link(link["token"], _Req(SHARER)))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run(share.preview_share(link["token"], _Req(TAKER)))
    # 410, not 404: "this was revoked" and "this never existed" need
    # different next steps from the person holding the link.
    assert exc.value.status_code == 410


def test_expired_link_stops_working(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    from shared.db.database import get_db
    with get_db() as db:
        db.execute("UPDATE agent_shares SET expires_at = CURRENT_TIMESTAMP "
                   "- INTERVAL '1 day' WHERE token=?", (link["token"],))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run(share.import_share(link["token"], _Req(TAKER)))
    assert exc.value.status_code == 410


def test_link_dies_with_the_source_agent(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    for slug in ("acme-inbound", "acme-outbound"):
        shutil.rmtree(_isolated / slug)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _run(share.preview_share(link["token"], _Req(TAKER)))
    assert exc.value.status_code == 410


def test_import_walks_past_every_name_already_taken(_isolated):
    """Slugs are globally unique across ALL tenants, so the minted name has to
    dodge the source family, the importer's own agents, and anyone else's —
    silently landing on a taken slug would write into a live agent's folder."""
    _seed_family(_isolated, "acme", SHARER)          # blocks "acme"
    _seed_family(_isolated, "acme-2", TAKER)         # blocks the next candidate
    _seed_family(_isolated, "acme-3", "third@example.com")   # and the one after
    link = _create_link("acme-inbound", SHARER)

    result = _run(share.import_share(link["token"], _Req(TAKER)))

    assert result["family"] == "acme-4"
    for slug in result["slugs"]:
        assert builder._slug_owner(slug) == TAKER


def test_preview_reports_the_plan_cap_instead_of_failing(_isolated,
                                                               monkeypatch):
    """At cap the popup must still render the agent AND say why Import is
    unavailable — an HTTP error would give the recipient a dead dialog."""
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)

    from shared.billing import entitlements
    from shared.billing.errors import CODE_CAP_AGENTS, Decision
    monkeypatch.setattr(entitlements, "check_agent_create",
                        lambda owner, family="": Decision.deny(
                            CODE_CAP_AGENTS, "Your plan includes 1 agent."))

    preview = _run(share.preview_share(link["token"], _Req(TAKER)))
    assert preview["can_import"] is False
    assert preview["code"] == CODE_CAP_AGENTS
    assert preview["agent"]["agent_name"] == "Asha"   # still fully rendered


def test_import_is_refused_at_the_plan_cap(_isolated, monkeypatch):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)

    from shared.billing.errors import BillingRefusal, CODE_CAP_AGENTS
    monkeypatch.setattr(builder, "_assert_agent_quota",
                        lambda owner, family="": (_ for _ in ()).throw(
                            BillingRefusal("Your plan includes 1 agent.",
                                           CODE_CAP_AGENTS)))
    with pytest.raises(BillingRefusal) as exc:
        _run(share.import_share(link["token"], _Req(TAKER)))
    assert exc.value.status_code == 402

    # Nothing was written, and the reservation row was released — otherwise
    # upgrading and retrying would hit "already imported" forever.
    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute("SELECT 1 FROM agents WHERE owner=?", (TAKER,)).fetchall()
        res = db.execute("SELECT 1 FROM agent_share_imports WHERE importer=?",
                         (TAKER,)).fetchall()
    assert not rows and not res


def test_a_failed_import_leaves_nothing_behind(_isolated, monkeypatch):
    """Half an imported pair is worse than none: a number could bind to the
    survivor and run the wrong direction's prompts."""
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)

    real = builder.write_agent_dir
    calls = {"n": 0}

    def flaky(name, spec, owner):
        calls["n"] += 1
        if calls["n"] == 2:          # second variant blows up mid-import
            raise RuntimeError("disk full")
        return real(name, spec, owner)

    monkeypatch.setattr(builder, "write_agent_dir", flaky)
    with pytest.raises(RuntimeError):
        _run(share.import_share(link["token"], _Req(TAKER)))

    from shared.db.database import get_db
    with get_db() as db:
        rows = db.execute("SELECT slug FROM agents WHERE owner=?", (TAKER,)).fetchall()
        res = db.execute("SELECT 1 FROM agent_share_imports WHERE importer=?",
                         (TAKER,)).fetchall()
    assert not rows, f"orphaned ownership rows: {[r['slug'] for r in rows]}"
    assert not res, "import reservation was not released"
    leftovers = [p.name for p in _isolated.iterdir() if p.name.startswith("acme-2")]
    assert not leftovers, f"orphaned folders: {leftovers}"


def test_import_count_tracks_reality(_isolated):
    _seed_family(_isolated, "acme", SHARER)
    link = _create_link("acme-inbound", SHARER)
    _run(share.import_share(link["token"], _Req(TAKER)))
    _run(share.import_share(link["token"], _Req("third@example.com")))

    res = _run(share.get_link(_Req(SHARER), slug="acme-inbound"))
    assert res["link"]["import_count"] == 2


def test_share_refuses_a_slug_that_is_not_a_slug(_isolated):
    """A traversal can never match an owned row, but it reaches
    `_AGENTS_DIR / slug` first — so the shape is rejected at the door."""
    from fastapi import HTTPException
    for bad in ("../../etc/passwd", "a/b", "acme;drop", "x" * 90):
        with pytest.raises(HTTPException) as exc:
            _create_link(bad, SHARER)
        assert exc.value.status_code == 400, bad
