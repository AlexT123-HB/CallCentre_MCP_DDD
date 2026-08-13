"""KB service interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.servers.kb.domain.models import KBResult


class KBService(ABC):
    @abstractmethod
    def answer(self, query: str, verified: bool, call_id: str = "") -> KBResult:
        ...
