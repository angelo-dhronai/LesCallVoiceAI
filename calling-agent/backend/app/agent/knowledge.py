"""Per-agent knowledge base: parse → chunk → embed → fast in-RAM retrieval.

Ingestion (parse/chunk/embed/build_index) runs in whichever process uploads a
document (the dashboard, or an ingest subprocess). The resulting vectors are
persisted under the agent's ``index/`` dir as a numpy ``.npz`` matrix plus a
``chunks.jsonl`` aligned to it. The live pipeline loads that matrix once into
RAM (KnowledgeIndex.load_from_disk) and answers queries with a single BLAS
matmul (cosine == dot product on L2-normalized vectors) — no network, ~10-20ms.

Heavy deps (sentence-transformers/torch, pypdf, python-docx) are imported
lazily so this module imports cleanly even where they aren't installed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

import numpy as np

from . import registry

logger = logging.getLogger("agent.knowledge")

MODEL_NAME = os.environ.get("KB_EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM = 384                 # all-MiniLM-L6-v2
CHUNK_CHARS = 800
CHUNK_OVERLAP = 150
MERGE_SHORT = 90                # merge consecutive fragments shorter than this
TOP_K = 3
MIN_SIM = 0.18                  # cosine floor — drop weakly-related chunks
SUPPORTED_EXT = {".pdf", ".docx", ".txt", ".md"}

_model = None
_model_lock = threading.Lock()


def get_model():
    """Lazy singleton SentenceTransformer, loaded once per process into RAM."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                logger.info("[KB] Loading embedding model %s …", MODEL_NAME)
                _model = SentenceTransformer(MODEL_NAME)
    return _model


# ---------------------------------------------------------------------------
# Parsing + chunking
# ---------------------------------------------------------------------------

def parse_document(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    # .txt / .md / anything text-like
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-primary chunking: each paragraph is its own chunk (best
    retrieval granularity — a specific query matches the specific paragraph),
    long paragraphs are hard-split with overlap, and very short fragments
    (headings, list items) are merged forward so we don't explode into noise."""
    text = "\n".join(line.rstrip() for line in text.splitlines())
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    raw: list[str] = []
    for para in paras:
        if len(para) <= size:
            raw.append(para)
        else:
            start = 0
            while start < len(para):
                raw.append(para[start:start + size])
                start += size - overlap
    # Merge consecutive short fragments (e.g. a heading followed by its detail).
    chunks: list[str] = []
    buf = ""
    for piece in raw:
        if buf and len(buf) < MERGE_SHORT and len(buf) + len(piece) + 1 <= size:
            buf = f"{buf}\n{piece}"
        else:
            if buf:
                chunks.append(buf)
            buf = piece
    if buf:
        chunks.append(buf)
    return [c for c in (s.strip() for s in chunks) if c]


def embed_texts(texts: list[str]) -> np.ndarray:
    """Return an L2-normalized float32 matrix [n, EMBED_DIM]."""
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    return vecs.astype(np.float32)


# ---------------------------------------------------------------------------
# Index build / load
# ---------------------------------------------------------------------------

def _chunks_file(agent_id: str) -> str:
    return os.path.join(registry.index_dir(agent_id), "chunks.jsonl")


def _emb_file(agent_id: str) -> str:
    return os.path.join(registry.index_dir(agent_id), "embeddings.npz")


def _meta_file(agent_id: str) -> str:
    return os.path.join(registry.index_dir(agent_id), "meta.json")


def build_index(agent_id: str) -> dict:
    """(Re)build the agent's vector index from all docs in its raw/ dir. Writes
    chunks.jsonl + embeddings.npz + meta.json atomically. Returns a summary."""
    raw = registry.raw_dir(agent_id)
    os.makedirs(registry.index_dir(agent_id), exist_ok=True)
    docs = _load_docs(agent_id)
    all_chunks: list[dict] = []
    for doc in docs:
        fpath = os.path.join(raw, doc["stored_name"])
        if not os.path.exists(fpath):
            continue
        try:
            text = parse_document(fpath)
            for i, ch in enumerate(chunk_text(text)):
                all_chunks.append({"doc_id": doc["id"], "filename": doc["filename"],
                                   "ord": i, "text": ch})
            doc["status"] = "ready"
            doc["chunk_count"] = sum(1 for c in all_chunks if c["doc_id"] == doc["id"])
        except Exception as exc:
            logger.error("[KB] parse failed for %s: %r", doc.get("filename"), exc)
            doc["status"] = "error"
            doc["error"] = str(exc)

    texts = [c["text"] for c in all_chunks]
    embeddings = embed_texts(texts)

    # Atomic writes (pipeline may read concurrently). Write through an explicit
    # file handle so numpy doesn't append ".npz" to the temp name.
    tmp_emb = _emb_file(agent_id) + ".tmp"
    with open(tmp_emb, "wb") as f:
        np.savez_compressed(f, embeddings=embeddings)
    os.replace(tmp_emb, _emb_file(agent_id))

    tmp_chunks = _chunks_file(agent_id) + ".tmp"
    with open(tmp_chunks, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    os.replace(tmp_chunks, _chunks_file(agent_id))

    meta = {"model": MODEL_NAME, "dim": EMBED_DIM, "n_chunks": len(all_chunks),
            "built_at": registry._now_iso()}
    tmp_meta = _meta_file(agent_id) + ".tmp"
    with open(tmp_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp_meta, _meta_file(agent_id))

    _save_docs(agent_id, docs)
    logger.info("[KB] Built index for %s: %d chunks from %d docs",
                agent_id, len(all_chunks), len(docs))
    return {"n_chunks": len(all_chunks), "n_docs": len(docs)}


# ---------------------------------------------------------------------------
# docs.json helpers (document metadata)
# ---------------------------------------------------------------------------

def _load_docs(agent_id: str) -> list[dict]:
    try:
        with open(registry.docs_path(agent_id), "r", encoding="utf-8") as f:
            return json.load(f).get("docs", [])
    except Exception:
        return []


def _save_docs(agent_id: str, docs: list[dict]) -> None:
    registry._atomic_write(registry.docs_path(agent_id), {"docs": docs})


def list_docs(agent_id: str) -> list[dict]:
    return _load_docs(agent_id)


def index_meta(agent_id: str) -> dict:
    try:
        with open(_meta_file(agent_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"n_chunks": 0}


# ---------------------------------------------------------------------------
# Retrieval index (loaded into pipeline RAM)
# ---------------------------------------------------------------------------

class KnowledgeIndex:
    def __init__(self, embeddings: np.ndarray, chunks: list[dict], mtime: float):
        self.embeddings = embeddings
        self.chunks = chunks
        self.mtime = mtime

    @classmethod
    def load_from_disk(cls, agent_id: str) -> "KnowledgeIndex | None":
        emb_path = _emb_file(agent_id)
        chunks_path = _chunks_file(agent_id)
        if not (os.path.exists(emb_path) and os.path.exists(chunks_path)):
            return None
        try:
            with np.load(emb_path) as data:
                embeddings = data["embeddings"]
            chunks = []
            with open(chunks_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        chunks.append(json.loads(line))
            if embeddings.shape[0] != len(chunks) or not len(chunks):
                return None
            return cls(embeddings, chunks, os.path.getmtime(emb_path))
        except Exception as exc:
            logger.error("[KB] index load failed for %s: %r", agent_id, exc)
            return None

    def search(self, query: str, k: int = TOP_K) -> list[dict]:
        if self.embeddings is None or not len(self.chunks):
            return []
        q = embed_texts([query])
        if not q.size:
            return []
        scores = self.embeddings @ q[0]
        k = min(k, len(scores))
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return [{**self.chunks[i], "score": float(scores[i])}
                for i in idx if scores[i] >= MIN_SIM]
