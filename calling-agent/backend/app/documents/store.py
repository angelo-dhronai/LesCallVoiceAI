"""
Document storage: save / list / delete per-agent documents.
Layout: agents/<agent_folder>/documents/<doc_id>/{metadata.json, text.txt, chunks.json}
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid
import yaml
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
_CHUNK_WORDS = 400
_CHUNK_OVERLAP = 60

# Cache: agent_id -> folder name (populated on first lookup per agent_id)
_id_to_folder: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Folder resolution
# ---------------------------------------------------------------------------

def get_folder_by_agent_id(agent_id: str) -> str:
    """Return the agents/ subfolder name whose agent.yaml matches agent_id. Cached."""
    if agent_id in _id_to_folder:
        return _id_to_folder[agent_id]
    for p in _AGENTS_DIR.glob("*/agent.yaml"):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            if data.get("agent_id") == agent_id:
                _id_to_folder[agent_id] = p.parent.name
                return p.parent.name
        except Exception:
            pass
    return ""


def _docs_dir(agent_folder: str) -> Path:
    return _AGENTS_DIR / agent_folder / "documents"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save_document(
    agent_folder: str,
    filename: str,
    content: bytes,
    extracted_text: str,
    org_name: str,
    doc_type: str,
    description: str,
) -> dict:
    """Persist a document and its chunks; return its metadata dict."""
    doc_id = uuid.uuid4().hex[:10]
    doc_dir = _docs_dir(agent_folder) / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Raw file
    (doc_dir / filename).write_bytes(content)
    # Extracted text
    (doc_dir / "text.txt").write_text(extracted_text, encoding="utf-8")
    # Chunks
    chunks = _chunk_text(extracted_text)
    (doc_dir / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
    )

    meta = {
        "doc_id": doc_id,
        "filename": filename,
        "org_name": org_name,
        "doc_type": doc_type,
        "description": description,
        "uploaded_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "chunk_count": len(chunks),
        "char_count": len(extracted_text),
    }
    (doc_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    _update_index(agent_folder, meta)
    logger.info(
        "[docs] saved doc_id=%s (%d chars, %d chunks) for agent=%s",
        doc_id, len(extracted_text), len(chunks), agent_folder,
    )
    return meta


def list_documents(agent_folder: str) -> list[dict]:
    index_path = _docs_dir(agent_folder) / "index.json"
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def delete_document(agent_folder: str, doc_id: str) -> bool:
    doc_dir = _docs_dir(agent_folder) / doc_id
    if not doc_dir.exists():
        return False
    shutil.rmtree(doc_dir)
    # Remove from index
    index_path = _docs_dir(agent_folder) / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index = [d for d in index if d["doc_id"] != doc_id]
            index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return True


def has_documents(agent_folder: str) -> bool:
    return bool(list_documents(agent_folder))


def get_document_text(agent_folder: str, doc_id: str) -> str:
    text_path = _docs_dir(agent_folder) / doc_id / "text.txt"
    if not text_path.exists():
        return ""
    return text_path.read_text(encoding="utf-8")


def get_all_chunks(agent_folder: str) -> list[dict]:
    """Return all text chunks from all documents for this agent."""
    all_chunks: list[dict] = []
    for doc in list_documents(agent_folder):
        chunks_path = _docs_dir(agent_folder) / doc["doc_id"] / "chunks.json"
        if not chunks_path.exists():
            continue
        try:
            chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            for c in chunks:
                c["doc_id"] = doc["doc_id"]
                c["doc_type"] = doc.get("doc_type", "")
            all_chunks.extend(chunks)
        except Exception:
            pass
    return all_chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _chunk_text(text: str) -> list[dict]:
    words = text.split()
    chunks: list[dict] = []
    i, cid = 0, 0
    while i < len(words):
        chunk_words = words[i : i + _CHUNK_WORDS]
        chunks.append({"chunk_id": cid, "text": " ".join(chunk_words), "word_start": i})
        cid += 1
        i += _CHUNK_WORDS - _CHUNK_OVERLAP
    return chunks


def _update_index(agent_folder: str, meta: dict) -> None:
    index_path = _docs_dir(agent_folder) / "index.json"
    index: list[dict] = []
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    index.append(meta)
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
