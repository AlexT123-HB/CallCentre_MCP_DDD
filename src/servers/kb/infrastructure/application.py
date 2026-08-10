"""KB infrastructure — DI wiring."""

from __future__ import annotations

from src.servers.kb.domain.service.services import DefaultKBService
from src.servers.kb.infrastructure.repositories import KBRepositoryImpl


def build_kb_service() -> DefaultKBService:
    return DefaultKBService(KBRepositoryImpl())
