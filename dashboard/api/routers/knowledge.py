"""Knowledge-base documents — per-tenant RAG source files.

Uploaded blobs are stored on disk under data/knowledge/<owner-slug>/ and indexed
by a row in the `documents` table. Indexing here is intentionally lightweight: a
file is accepted, persisted, and marked `indexed` (progress 100). Wiring the
stored text into the live agent's retrieval is a separate pipeline concern; this
router owns storage + lifecycle so the dashboard's Knowledge Base view is real.

Every row is scoped to the logged-in user via `owner`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from shared.db.database import get_db, _REPO_ROOT
from dashboard.api.auth import current_owner

router = APIRouter()

KNOWLEDGE_ROOT = _REPO_ROOT / "data" / "knowledge"
MAX_BYTES = 25 * 1024 * 1024  # 25 MB, matches the upload modal copy
_EXT_TYPE = {".pdf": "pdf", ".csv": "csv", ".docx": "docx", ".doc": "docx",
             ".txt": "txt", ".md": "txt"}


def _slug(email: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in (email or "local").split("@")[0])[:24] or "local"


def _doc_dir(owner: str) -> Path:
    d = KNOWLEDGE_ROOT / _slug(owner)
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/knowledge")
def list_documents(owner: str = Depends(current_owner)) -> list[dict]:
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM documents WHERE owner=? ORDER BY id DESC", (owner,))]


@router.post("/knowledge/upload")
async def upload_document(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    agent_id: Optional[int] = Form(None),
    owner: str = Depends(current_owner),
) -> dict:
    """Accept a file (multipart) or a URL, persist it, and index the row."""
    if url:
        name = url.strip()
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO documents (owner, agent_id, name, doc_type, source_url, "
                "status, progress) VALUES (?,?,?,?,?,?,?) RETURNING id",
                (owner, agent_id, name, "url", name, "indexed", 100))
            doc_id = cur.fetchone()["id"]
            row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return dict(row)

    if file is None or not (file.filename or "").strip():
        raise HTTPException(400, "provide a file or a url")

    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file exceeds 25 MB limit")

    orig = os.path.basename(file.filename)
    ext = os.path.splitext(orig)[1].lower()
    doc_type = _EXT_TYPE.get(ext, "txt")

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO documents (owner, agent_id, name, doc_type, size_bytes, "
            "status, progress) VALUES (?,?,?,?,?,?,?) RETURNING id",
            (owner, agent_id, orig, doc_type, len(data), "pending", 0))
        doc_id = cur.fetchone()["id"]

    stored_name = f"{doc_id}{ext or '.bin'}"
    (_doc_dir(owner) / stored_name).write_bytes(data)

    with get_db() as db:
        db.execute(
            "UPDATE documents SET stored_name=?, status=?, progress=? WHERE id=?",
            (stored_name, "indexed", 100, doc_id))
        row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(row)


def _owned_doc(doc_id: int, owner: str) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM documents WHERE id=? AND owner=?",
                         (doc_id, owner)).fetchone()
    if not row:
        raise HTTPException(404, "document not found")
    return dict(row)


@router.get("/knowledge/{doc_id}/download")
def download_document(doc_id: int, owner: str = Depends(current_owner)):
    doc = _owned_doc(doc_id, owner)
    if not doc.get("stored_name"):
        raise HTTPException(404, "no file for this document")
    path = _doc_dir(owner) / doc["stored_name"]
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(path, filename=doc["name"])


@router.delete("/knowledge/{doc_id}")
def delete_document(doc_id: int, owner: str = Depends(current_owner)) -> dict:
    doc = _owned_doc(doc_id, owner)
    if doc.get("stored_name"):
        try:
            (_doc_dir(owner) / doc["stored_name"]).unlink(missing_ok=True)
        except OSError:
            pass
    with get_db() as db:
        db.execute("DELETE FROM documents WHERE id=? AND owner=?", (doc_id, owner))
    return {"deleted": doc_id}
