"""KB domain models — pure. Shared knowledge, not caller-owned."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class KBEntry:
    id: str
    question: str
    short_answer: str
    aliases: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    sensitive: bool = False          # sensitive entries need a verified caller

    def visible_to(self, verified: bool) -> bool:
        """Sensitivity gate: sensitive entries require verification."""
        return verified or not self.sensitive


@dataclass
class KBResult:
    found: bool
    entries: list = field(default_factory=list)   # was: entry (single) → now top-k
    score: float = 0.0                            # top score (for logging/threshold)
    needs_verification: bool = False
