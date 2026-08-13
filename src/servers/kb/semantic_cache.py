"""
Semantic cache for the KB — serve a repeat/rephrased question from a previous
answer instead of re-running retrieval.

"Does HeartBug work on a plane?" / "Can I fly with it?" / "Is it OK on flights?"
are the same question. We embed the query (reusing the KB's own model), cosine-
compare against previously answered queries, and on a close-enough match return the
cached answer — no retrieval, no LLM.

Design notes:
  • REUSE the KB embedder (embed_query) — no second model load.
  • Vectors are normalized (the KB encodes with normalize_embeddings=True), so
    cosine = dot product. The store is small (FAQs), so a linear scan is sub-ms;
    no vector DB needed.
  • Cache ALL answers (no gating). The cache only ever holds generic KB answers.
  • Bounded (LRU-ish cap) so the scan stays cheap; TTL so stale answers age out.
  • OFF by default — enable with HEARTBUG_KB_CACHE=1.
"""
from __future__ import annotations

import os
import time
import logging

import numpy as np

from src.servers.kb.infrastructure.knowledge_base.embedding_kb import embed_query

_log = logging.getLogger("heartbug")

# ── config (flags, same discipline as the rest of the system) ──────────────────
_ENABLED   = os.getenv("HEARTBUG_KB_CACHE", "0") == "1"
# a hit needs NEAR-identical intent — higher than the KB retrieval floor (0.35),
# because serving the wrong FAQ is worse than a miss. 0.92 per semantic-cache norms.
_THRESHOLD = float(os.getenv("HEARTBUG_KB_CACHE_THRESHOLD", "0.95"))
_TTL       = float(os.getenv("HEARTBUG_KB_CACHE_TTL", str(24 * 3600)))   # 24h
_MAX       = int(os.getenv("HEARTBUG_KB_CACHE_MAX", "500"))              # cap entries

# # announce state at import so the startup log makes the config obvious — no more
# # guessing why KB-CACHE lines are missing (flag off? embedder absent?).
# logging.getLogger("heartbug").info(
#     f"KB-CACHE init: enabled={_ENABLED} threshold={_THRESHOLD} ttl={_TTL}")


class _Entry:
    __slots__ = ("vec", "answer", "ts")
    def __init__(self, vec, answer, ts):
        self.vec = vec
        self.answer = answer
        self.ts = ts


# in-process store: list of _Entry. Small (FAQs), so a linear cosine scan is sub-ms.
_STORE: list[_Entry] = []


def _fresh(e: _Entry, now: float) -> bool:
    return (now - e.ts) < _TTL


def get(query: str, call_id: str = ""):
    """Return a cached answer dict for a semantically-matching query, or None.

    None means "not a hit" — the caller runs the normal KB path."""
    if not _ENABLED:
        return None
    tag = f"[{call_id}] " if call_id else ""
    qv = embed_query(query)
    if qv is None:
        _log.info(f"{tag}KB-CACHE skip: embed_query returned None (no embedder?)")
        return None
    now = time.time()
    best_score, best = 0.0, None
    for e in _STORE:
        if not _fresh(e, now):
            continue
        score = float(np.dot(qv, e.vec))     # both normalized → dot = cosine
        if score > best_score:
            best_score, best = score, e
    if best is not None and best_score >= _THRESHOLD:
        _log.info(f"{tag}KB-CACHE HIT score={best_score:.3f} q={query!r}")
        return best.answer
    _log.info(f"{tag}KB-CACHE MISS best={best_score:.3f} q={query!r}")
    return None


def put(query: str, answer: dict, call_id: str = "") -> None:
    """Store an answer for future semantic matches. Caller decides what's cacheable
    (we cache all real answers; misses/needs-verify are not passed here)."""
    if not _ENABLED:
        return
    qv = embed_query(query)
    tag = f"[{call_id}] " if call_id else ""
    if qv is None:
        return
    now = time.time()
    # dedupe: if a near-identical query is already cached, refresh it instead of
    # storing a 2nd near-duplicate (keeps the scan small).
    for e in _STORE:
        if float(np.dot(qv, e.vec)) >= _THRESHOLD:
            e.answer, e.ts = answer, now
            return
    _STORE.append(_Entry(qv, answer, now))
    # bound the store: drop oldest when over cap (cheap LRU-by-age)
    if len(_STORE) > _MAX:
        _STORE.sort(key=lambda e: e.ts)
        del _STORE[: len(_STORE) - _MAX]    
    _log.info(f"{tag}KB-CACHE PUT q={query!r} store={len(_STORE)}")


def clear() -> None:
    """Flush the cache — call when the KB source/index is rebuilt so a stale answer
    can't outlive a KB update (content-triggered invalidation)."""
    _STORE.clear()
    _log.info("KB-CACHE cleared")