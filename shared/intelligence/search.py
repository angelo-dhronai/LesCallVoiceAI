"""Hybrid semantic call search — the Call History natural-language filter.

Two retrieval arms run over call_embeddings, both tenant-scoped:

    semantic — HNSW cosine over 768-dim gemini-embedding-001 vectors
               (handles paraphrase + Hinglish/Gujarati code-mix)
    lexical  — FTS websearch_to_tsquery('simple') over the generated tsv
               column, with a trigram-similarity fallback for short/misspelled
               queries (handles exact terms like "3BHK")

Results fuse at the CALL level with Reciprocal Rank Fusion (k=60): each call
is ranked by its best chunk in each arm; RRF rewards calls found by both.

Degrades gracefully: if the embedding API is down (or embeddings disabled),
the semantic arm is skipped and results are lexical-only with degraded=True.
Embedding the query is the only per-search API cost (~100ms); everything else
is two index scans.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.db.database import get_db
from shared.intelligence import config, embed

log = logging.getLogger("intel.search")

RRF_K = 60
ARM_LIMIT = 40


def _semantic_arm(owner: str, query_vec: list[float], limit: int) -> list[dict]:
    lit = embed._vec_literal(query_vec)
    with get_db() as db:
        return [dict(r) for r in db.execute(
            """SELECT DISTINCT ON (call_id)
                      call_id, content, 1 - (embedding <=> ?::vector) AS score
                 FROM call_embeddings
                WHERE owner = ? AND embedding IS NOT NULL
                ORDER BY call_id, embedding <=> ?::vector
                LIMIT 400""",
            (lit, owner, lit),
        ).fetchall()][:limit * 4]


def _order_semantic(rows: list[dict], limit: int) -> list[dict]:
    return sorted(rows, key=lambda r: -(r["score"] or 0))[:limit]


def _lexical_arm(owner: str, query: str, limit: int) -> list[dict]:
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            """SELECT DISTINCT ON (call_id)
                      call_id, content,
                      ts_rank(tsv, websearch_to_tsquery('simple', ?)) AS score
                 FROM call_embeddings
                WHERE owner = ?
                  AND tsv @@ websearch_to_tsquery('simple', ?)
                ORDER BY call_id, ts_rank(tsv, websearch_to_tsquery('simple', ?)) DESC
                LIMIT 400""",
            (query, owner, query, query),
        ).fetchall()]
        if not rows and len(query) >= 4:
            # trigram fallback for misspellings / partial words. The %% is the
            # pg_trgm similarity operator escaped for psycopg's placeholder
            # parser (the qmark wrapper only rewrites '?', not '%').
            rows = [dict(r) for r in db.execute(
                """SELECT DISTINCT ON (call_id)
                          call_id, content, similarity(content, ?) AS score
                     FROM call_embeddings
                    WHERE owner = ? AND content %% ?
                    ORDER BY call_id, similarity(content, ?) DESC
                    LIMIT 200""",
                (query, owner, query, query),
            ).fetchall()]
    return sorted(rows, key=lambda r: -(r["score"] or 0))[:limit]


def rrf_fuse(arms: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion at the call level; keeps the best snippet."""
    scores: dict[int, float] = {}
    snippets: dict[int, str] = {}
    for arm in arms:
        for rank, row in enumerate(arm):
            cid = row["call_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            snippets.setdefault(cid, row.get("content") or "")
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [{"call_id": cid, "rrf": score, "snippet": snippets.get(cid, "")}
            for cid, score in ranked]


async def search_calls(owner: str, query: str, limit: int = 25) -> dict[str, Any]:
    """Returns {results: [{call_id, rrf, snippet}], degraded: bool}.

    Event-loop safe: the embedding call is async httpx; the (sync) index scans
    run in worker threads so a caller inside FastAPI never blocks the loop.
    """
    import asyncio

    query = (query or "").strip()
    if not query:
        return {"results": [], "degraded": False}

    degraded = False
    semantic: list[dict] = []
    if config.EMBED_ENABLED and config.EMBED_API_KEY:
        try:
            qvec = await embed.embed_query(query)
            rows = await asyncio.to_thread(_semantic_arm, owner, qvec, ARM_LIMIT)
            semantic = _order_semantic(rows, ARM_LIMIT)
        except Exception as exc:
            log.warning("semantic arm unavailable, lexical-only: %r", exc)
            degraded = True
    else:
        degraded = True

    lexical = await asyncio.to_thread(_lexical_arm, owner, query, ARM_LIMIT)
    fused = rrf_fuse([semantic, lexical])[:limit]
    return {"results": fused, "degraded": degraded}
