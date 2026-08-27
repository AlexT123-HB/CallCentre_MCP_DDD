"""Wire the concrete booking pieces into a service (composition root)."""
from __future__ import annotations

from src.shared.cache import get_cache
from src.shared import booking_handoff as handoff
from src.servers.booking.infrastructure.repositories import CacheBookingRepository
from src.servers.booking.domain.service.services import DefaultBookingService


def build_booking_service() -> DefaultBookingService:
    repo = CacheBookingRepository(get_cache())
    return DefaultBookingService(repo, handoff_writer=handoff.write_booking_row)
