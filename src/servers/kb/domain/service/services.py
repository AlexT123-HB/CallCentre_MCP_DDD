"""
KB service — answer a general question from the knowledge base.

Two policies live here (business rules, not infrastructure):
  1. Confidence threshold — a weak semantic match is treated as a MISS, so the
     agent says "I don't have that" rather than returning an irrelevant entry.
  2. Sensitivity gate — a matched entry flagged sensitive is only returned to a
     VERIFIED caller; otherwise it's reported as needing verification.
"""

from __future__ import annotations

import os
import logging

from src.servers.kb.domain.models import KBResult
from src.servers.kb.domain.repository.interfaces import KBRepository
from src.servers.kb.domain.service.interfaces import KBService

# below this cosine similarity, we don't trust the match → treat as a miss
_MIN_SCORE = float(os.getenv("KB_MIN_SCORE", "0.65"))
_log = logging.getLogger("heartbug")


class DefaultKBService(KBService):
    def __init__(self, repository: KBRepository) -> None:
        self._repo = repository

    def answer(self, query: str, verified: bool, call_id: str = "") -> KBResult:
        tag = f"[{call_id}] " if call_id else ""
        matches = self._repo.search(query, top_k=5)
        if not matches:
            _log.info(f"KB MISS (no match) for q={query!r}")
            return KBResult(found=False)

        best_score = matches[0][1]                     # ranked → [0] is the best
        if best_score < _MIN_SCORE:
            _log.info(f"{tag}KB → MISS (best {best_score:.3f} < {_MIN_SCORE}) q={query!r}")
            return KBResult(found=False, score=best_score)

        # Keep only matches above threshold, then sensitivity-filter for this caller
        strong_matches = [(entry, score) for entry, score in matches if score >= _MIN_SCORE]
        visible_matches = [(entry, score) for entry, score in strong_matches
                           if entry.visible_to(verified)]

        if not visible_matches:
            # strong matches exist but ALL are sensitive and the caller isn't verified
            _log.info(f"{tag}KB → NEEDS_VERIFY (all strong matches sensitive) q={query!r}")
            return KBResult(found=False, score=best_score, needs_verification=True)

        for entry, score in visible_matches:
            _log.info(f"{tag}KB RETRIEVE entry={entry.id!r} matched_q={entry.question!r} "
                      f"score={score:.3f}")
        _log.info(f"{tag}KB → ANSWER {len(visible_matches)} entries best={best_score:.3f}")
        return KBResult(found=True, entries=[e for e, _ in visible_matches], score=best_score)