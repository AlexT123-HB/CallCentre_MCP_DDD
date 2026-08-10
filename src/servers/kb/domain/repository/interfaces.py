"""KB repository interface — semantic search contract for the domain."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from src.servers.kb.domain.models import KBEntry


class KBRepository(ABC):
    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[Tuple[KBEntry, float]]:
        """Return the best-matching entries with their similarity scores, ranked."""
        ...
