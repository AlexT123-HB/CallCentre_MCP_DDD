"""Cache-backed BookingRepository — the concrete storage adapter.

Implements the domain's BookingRepository port over the shared cache (Redis). This
is the ONLY place that knows bookings live in the cache under `callback:<id>`.
Swapping to a real DB table = a new implementation of this port; the domain service
above is untouched.
"""
from __future__ import annotations

from typing import List, Optional

from src.servers.booking.domain.models import Booking
from src.servers.booking.domain.repository.interfaces import BookingRepository

_KEY_PREFIX = "callback:"


class CacheBookingRepository(BookingRepository):
    def __init__(self, cache):
        self._cache = cache

    def _key(self, patient_id: str) -> str:
        return f"{_KEY_PREFIX}{patient_id}"

    def get_for_patient(self, patient_id: str) -> Optional[Booking]:
        raw = self._cache.get(self._key(patient_id))
        return Booking.from_dict(raw) if raw else None

    def save(self, booking: Booking) -> None:
        self._cache.set(self._key(booking.patient_id), booking.to_dict())

    def delete_for_patient(self, patient_id: str) -> None:
        self._cache.delete(self._key(patient_id))

    def list_all(self) -> List[Booking]:
        try:
            ns = self._cache.get_namespace(_KEY_PREFIX)     # {key: record}
        except Exception:                                   # noqa: BLE001
            return []
        out: List[Booking] = []
        for rec in (ns or {}).values():
            if isinstance(rec, dict) and rec.get("booking_id"):
                out.append(Booking.from_dict(rec))
        return out
