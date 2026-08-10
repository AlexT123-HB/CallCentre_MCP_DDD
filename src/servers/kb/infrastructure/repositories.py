"""
KB infrastructure — concrete repository over the embedding index.

Adapts the existing embedding_kb module (semantic vectors + keyword tokens) to the
domain's KBRepository interface. Hybrid score = semantic cosine + small keyword
overlap bonus, so both meaning and exact-term matches contribute. Maps raw dict
entries to KBEntry domain models.
"""

from __future__ import annotations

from typing import List, Tuple

from src.servers.kb.domain.models import KBEntry
from src.servers.kb.domain.repository.interfaces import KBRepository
from src.servers.kb.infrastructure.knowledge_base import embedding_kb as ekb


def _to_entry(raw: dict) -> KBEntry:
    tags = raw.get("tags", [])
    if isinstance(tags, str):
        tags = tags.split()
    return KBEntry(
        id=raw.get("id", ""),
        question=raw.get("question", ""),
        short_answer=raw.get("short_answer", ""),
        aliases=raw.get("aliases", []) or [],
        tags=tags or [],
        sensitive=bool(raw.get("sensitive", False)),
    )


class KBRepositoryImpl(KBRepository):
    def __init__(self) -> None:
        self._entries = ekb._KB_ENTRIES                      # loaded at import

    def search(self, query: str, top_k: int = 1) -> List[Tuple[KBEntry, float]]:
        if not self._entries:
            return []
        sem = ekb._semantic_score(query)                     # 1D array or None
        q_tokens = ekb._tokenize(query)
        scored = []
        for i, raw in enumerate(self._entries):
            semantic = float(sem[i]) if sem is not None else 0.0
            # small keyword-overlap bonus (hybrid): fraction of query tokens seen
            entry_tokens = ekb._tokenize(ekb._entry_text(raw))
            overlap = (len(q_tokens & entry_tokens) / len(q_tokens)) if q_tokens else 0.0
            score = semantic + 0.15 * overlap
            scored.append((_to_entry(raw), score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
