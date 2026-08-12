"""Configuration for the document RAG pipeline.

Every knob is an env var, in the same shape as `shared/intelligence/config.py`,
so retuning retrieval never needs a code edit. Values are read at import; the
runtime that serves calls is restarted to apply them
(`docker restart lescall-runtime`).

WHERE THE DEFAULTS COME FROM
----------------------------
The thresholds below are not round numbers picked for looking sensible. They
were measured against `rag_eval/` on the real corpus — see
`rag_eval/baselines/legacy.json` — and the two that decide whether a caller
gets an answer are the ones with the most measurement behind them:

ABSTAIN_MIN_SCORE = 0.25
  Kept at the pre-existing floor deliberately. Raising it does NOT buy
  abstention accuracy: on the golden set the cosine scores of answerable and
  unanswerable questions overlap almost completely (answerable 0.26-0.74,
  unanswerable 0.27-0.71 — "how many employees does DhronAI have?" scores
  0.71, higher than most answerable questions, because it is *about* the
  document's subject even though the document never mentions employees).
  Measured sweep, 37 golden questions:

      floor 0.25 -> catches 0/7 no-answer,  wrongly refuses 0/28 answerable
      floor 0.30 -> catches 1/7,            wrongly refuses 3/28
      floor 0.40 -> catches 2/7,            wrongly refuses 15/28
      floor 0.50 -> catches 3/7,            wrongly refuses 20/28

  A similarity floor is therefore the wrong instrument for "is the answer in
  here" and is used only for what it can do: drop chunks that are about
  something else entirely. Semantic absence is caught at generation time by
  the grounding contract (see `grounding.py`), and both layers are measured
  separately by the evaluation.

  Over-refusal is not a symmetric cost here: this platform has already shipped
  one over-deflection regression (a caller asking about a city the catalogue
  covered was told "I'll check with the team" three times). A caller who is
  wrongly refused hangs up; a caller given a weak chunk still hears a grounded
  answer or an honest "let me check".
"""

from __future__ import annotations

import os


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# --- master switch ---------------------------------------------------------
ENABLED = _flag("RAG_ENABLED", True)

# --- embeddings ------------------------------------------------------------
# Provider/model/dimension are recorded INTO the index at build time and
# re-checked at query time; changing them here does not silently reinterpret
# vectors built with something else (see embeddings.IndexMeta).
EMBED_PROVIDER = os.environ.get("RAG_EMBED_PROVIDER", "openai")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = _int("RAG_EMBED_DIM", 1536)
EMBED_API_KEY_ENV = os.environ.get("RAG_EMBED_API_KEY_ENV", "OPENAI_API_KEY")
EMBED_BASE_URL = os.environ.get("RAG_EMBED_BASE_URL", "")
# Hard cap on the query embedding. A live call cannot wait: past this the turn
# proceeds on the lexical arm alone rather than holding the caller in silence.
EMBED_TIMEOUT_S = _float("RAG_EMBED_TIMEOUT_S", 2.0)
EMBED_MAX_RETRIES = _int("RAG_EMBED_MAX_RETRIES", 1)

# --- chunking --------------------------------------------------------------
# 250 words, measured (`rag_eval.sweep`, 37 golden questions, 10 documents):
#
#     chunk  Recall@1  Recall@3  MRR   NDCG@5   Precision@3
#       400    0.70      1.00    0.88   0.89       0.40
#       250    0.79      0.98    0.89   0.90       0.42
#       150    0.54      0.98    0.77   0.81       0.48
#
# 400 buries the answering sentence among three other topics, so the right
# CHUNK ranks below a wrong one even when the right DOCUMENT wins. 150 goes too
# far the other way: the fragment that matches loses the context that made it
# findable, and Recall@1 collapses. 250 also shrinks the prompt — three
# 250-word chunks is a third less context per turn than three 400-word ones, on
# a platform where prompt input is ~98% of the LLM bill.
CHUNK_WORDS = _int("RAG_CHUNK_WORDS", 250)
CHUNK_OVERLAP = _int("RAG_CHUNK_OVERLAP", 60)

# --- retrieval / re-ranking ------------------------------------------------
# Retrieve wide, then narrow: the vector arm alone put the right chunk first
# only 66% of the time on the golden set, while the right chunk was inside the
# top 20 candidates 100% of the time.
RETRIEVAL_TOP_K = _int("RAG_RETRIEVAL_TOP_K", 20)
# 3, not 5. Measured, K=5 buys Recall@5 1.00 against Recall@3 0.98 — one golden
# question — and costs two extra chunks of prompt on EVERY turn of every call.
# MRR and NDCG are identical at both. Weak evidence is also pruned below the
# score floor, so a K of 3 is a ceiling, not a quota.
FINAL_TOP_K = _int("RAG_FINAL_TOP_K", 3)
RERANK_ENABLED = _flag("RAG_RERANK_ENABLED", True)
# "rrf" is the built-in hybrid re-ranker: BM25 lexical arm fused with the
# vector arm by Reciprocal Rank Fusion, then MMR for redundancy. It adds no
# dependency and no network call — measured at well under a millisecond, which
# is what makes it usable on a live voice turn where a cross-encoder is not.
RERANK_MODEL = os.environ.get("RAG_RERANK_MODEL", "rrf")
RRF_K = _int("RAG_RRF_K", 60)
# 1.0 = pure relevance, below 1.0 trades relevance for diversity.
#
# SHIPPED OFF (1.0), against the textbook. MMR assumes the top results are
# near-duplicates competing for one slot. Here they are frequently *joint*
# evidence: chunking overlaps by 60 words, and questions like "which product
# fits a bank that can't send data out" are answered by two adjacent chunks
# together. Suppressing the second one as redundant removes half the answer.
# Measured at every grid point — MMR 0.75 was worse than 1.0 in all 12 pairs:
#
#     chunk 400, K=3:  Recall@3 0.80 vs 1.00,  MRR 0.79 vs 0.88
#     chunk 250, K=3:  Recall@3 0.91 vs 0.98,  MRR 0.88 vs 0.89
#     chunk 150, K=3:  Recall@3 0.88 vs 0.98,  MRR 0.70 vs 0.77
#
# Kept configurable because the calculus flips for a corpus of genuinely
# duplicated documents (the same brochure uploaded five times), which this
# platform will eventually see.
MMR_LAMBDA = _float("RAG_MMR_LAMBDA", 1.0)
# Weight of the lexical arm in the fusion (1.0 = equal to the vector arm).
# Halving it was worse at every grid point (Recall@1 0.79 -> 0.71 at the
# shipped chunking): the lexical arm is what fixes exact-term questions
# ("3BHK", "ISO 27001", a product name) that paraphrase-tolerant embeddings
# blur together.
LEXICAL_WEIGHT = _float("RAG_LEXICAL_WEIGHT", 1.0)

# --- abstention ------------------------------------------------------------
ABSTENTION_ENABLED = _flag("RAG_ABSTENTION_ENABLED", True)
ABSTAIN_MIN_SCORE = _float("RAG_ABSTENTION_THRESHOLD", 0.25)
# A turn must carry at least this many informative (non-stopword) characters
# before it is worth an embedding call at all. "hmm", "ok", "haan" are turns,
# not questions, and retrieving for them injected three unrelated chunks into
# the prompt on the old path.
ABSTAIN_MIN_QUERY_TERMS = _int("RAG_ABSTENTION_MIN_QUERY_TERMS", 1)
ABSTAIN_MIN_QUERY_CHARS = _int("RAG_ABSTENTION_MIN_QUERY_CHARS", 4)
# Abstain when NONE of the query's informative terms appear anywhere in the
# retrieved evidence. Deliberately the weakest possible lexical rule: measured
# at zero false refusals on the golden set, where any stricter coverage
# threshold started refusing answerable questions (synonymy — "servers" vs
# "infrastructure" — makes term overlap a poor proxy for answerability).
ABSTAIN_ON_ZERO_OVERLAP = _flag("RAG_ABSTENTION_ZERO_OVERLAP", True)

# --- context assembly ------------------------------------------------------
# Cap on the evidence block. Context is ~98% of the LLM bill on this platform
# and every chunk is re-sent on every turn of the call.
CONTEXT_MAX_CHARS = _int("RAG_CONTEXT_MAX_CHARS", 4000)
CITATIONS_ENABLED = _flag("RAG_CITATIONS_ENABLED", True)
# Whether the agent may SPEAK a citation. Off: this is a phone call, and "as
# per document two, section three" is not something a human says out loud. The
# citation is structured data on the turn record, not dialogue.
SPOKEN_CITATIONS = _flag("RAG_SPOKEN_CITATIONS", False)

# --- semantic cache --------------------------------------------------------
CACHE_ENABLED = _flag("RAG_SEMANTIC_CACHE_ENABLED", True)
CACHE_TTL_S = _int("RAG_CACHE_TTL_S", 900)
CACHE_MAX_ENTRIES = _int("RAG_CACHE_MAX_ENTRIES", 512)
# Cosine similarity above which two queries are treated as the same question.
# 0.95 is intentionally high: this cache returns EVIDENCE, and a near-miss
# serves the wrong evidence for a subtly different question.
CACHE_SIMILARITY_THRESHOLD = _float("RAG_CACHE_SIMILARITY_THRESHOLD", 0.95)

# --- observability ---------------------------------------------------------
TRACING_ENABLED = _flag("RAG_TRACING_ENABLED", True)
TRACE_DIR = os.environ.get("RAG_TRACE_DIR", "")
# Never on by default: chunk text is tenant document content.
TRACE_INCLUDE_TEXT = _flag("RAG_TRACE_INCLUDE_TEXT", False)
COST_TRACKING_ENABLED = _flag("RAG_COST_TRACKING_ENABLED", True)


def snapshot() -> dict:
    """The live configuration, for the trace record and the /documents API."""
    return {
        "enabled": ENABLED,
        "embed_provider": EMBED_PROVIDER,
        "embed_model": EMBED_MODEL,
        "embed_dim": EMBED_DIM,
        "chunk_words": CHUNK_WORDS,
        "chunk_overlap": CHUNK_OVERLAP,
        "retrieval_top_k": RETRIEVAL_TOP_K,
        "final_top_k": FINAL_TOP_K,
        "rerank_enabled": RERANK_ENABLED,
        "rerank_model": RERANK_MODEL,
        "abstention_enabled": ABSTENTION_ENABLED,
        "abstain_min_score": ABSTAIN_MIN_SCORE,
        "cache_enabled": CACHE_ENABLED,
        "cache_similarity_threshold": CACHE_SIMILARITY_THRESHOLD,
    }
