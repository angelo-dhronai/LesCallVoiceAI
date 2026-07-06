"""
Chunk embeddings and similarity search for RAG context injection.
Primary: OpenAI text-embedding-3-small, vectors stored as .npy per document.
Fallback: keyword overlap scoring when OpenAI key is absent.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parents[3] / "agents"
_EMBED_MODEL = "text-embedding-3-small"
_TOP_K = 3
_MIN_SIM = 0.25   # cosine threshold below which chunks are considered irrelevant

# Module-level OpenAI client (lazy init, reused across calls)
_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        import openai
        _openai_client = openai.OpenAI(api_key=api_key)
        return _openai_client
    except ImportError:
        return None


def embed_document(agent_folder: str, doc_id: str) -> bool:
    """
    Generate and save embeddings for all chunks of a document.
    Called after upload. Safe to re-call (overwrites existing .npy).
    """
    doc_dir = _AGENTS_DIR / agent_folder / "documents" / doc_id
    chunks_path = doc_dir / "chunks.json"
    if not chunks_path.exists():
        return False

    try:
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    if not chunks:
        return True  # Nothing to embed

    texts = [c["text"] for c in chunks]
    vectors = _embed_batch(texts)
    if vectors is None:
        logger.info("[docs/emb] OpenAI unavailable for %s/%s — will use keyword fallback", agent_folder, doc_id)
        return False

    np.save(str(doc_dir / "embeddings.npy"), vectors)
    logger.info("[docs/emb] Embedded %d chunks for %s/%s", len(chunks), agent_folder, doc_id)
    return True


def retrieve_context(agent_folder: str, query: str, top_k: int = _TOP_K) -> str:
    """
    Return a ━━ DOCUMENT CONTEXT ━━ block with the most relevant chunks,
    or empty string if nothing is relevant enough.
    """
    from . import store
    docs = store.list_documents(agent_folder)
    if not docs:
        return ""

    query_vec = _embed_batch([query])

    if query_vec is not None:
        results = _semantic_search(agent_folder, docs, query_vec[0], top_k)
    else:
        results = _keyword_search(agent_folder, docs, query, top_k)

    if not results:
        return ""

    lines = ["━━ DOCUMENT CONTEXT ━━"]
    for text, doc_type in results:
        prefix = f"[{doc_type}] " if doc_type else ""
        lines.append(f"{prefix}{text.strip()}")
    lines.append(
        "Above context is from uploaded business documents. "
        "Weave relevant facts naturally into your response — do not quote or cite them directly."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal search helpers
# ---------------------------------------------------------------------------

def _semantic_search(
    agent_folder: str,
    docs: list[dict],
    query_vec: np.ndarray,
    top_k: int,
) -> list[tuple[str, str]]:
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    results: list[tuple[float, str, str]] = []

    for doc in docs:
        doc_id = doc["doc_id"]
        doc_dir = _AGENTS_DIR / agent_folder / "documents" / doc_id
        emb_path = doc_dir / "embeddings.npy"
        chunks_path = doc_dir / "chunks.json"
        if not emb_path.exists() or not chunks_path.exists():
            continue
        try:
            emb = np.load(str(emb_path))
            chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            norms = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-10
            scores = (emb / norms) @ q_norm
            for idx, score in enumerate(scores):
                if float(score) >= _MIN_SIM and idx < len(chunks):
                    results.append((float(score), chunks[idx]["text"], doc.get("doc_type", "")))
        except Exception as exc:
            logger.debug("[docs/emb] search error for %s: %s", doc_id, exc)

    results.sort(reverse=True)
    return [(t, dt) for _, t, dt in results[:top_k]]


def _keyword_search(
    agent_folder: str,
    docs: list[dict],
    query: str,
    top_k: int,
) -> list[tuple[str, str]]:
    from . import store
    all_chunks = store.get_all_chunks(agent_folder)
    query_words = set(query.lower().split())
    if not query_words:
        return []

    scored: list[tuple[int, str, str]] = []
    for chunk in all_chunks:
        overlap = len(query_words & set(chunk["text"].lower().split()))
        if overlap > 0:
            scored.append((overlap, chunk["text"], chunk.get("doc_type", "")))

    scored.sort(reverse=True)
    return [(t, dt) for _, t, dt in scored[:top_k]]


# ---------------------------------------------------------------------------
# Embedding API call
# ---------------------------------------------------------------------------

def _embed_batch(texts: list[str]) -> np.ndarray | None:
    client = _get_openai_client()
    if client is None:
        return None
    try:
        response = client.embeddings.create(model=_EMBED_MODEL, input=texts)
        return np.array([e.embedding for e in response.data], dtype=np.float32)
    except Exception as exc:
        logger.warning("[docs/emb] OpenAI embedding error: %s", exc)
        return None
