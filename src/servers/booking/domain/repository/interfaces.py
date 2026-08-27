"""Booking repository interface — the domain's contract for booking storage.

Storage-agnostic: the domain service depends on THIS, not on the cache. Swapping
the cache-backed store for a real DB table is a new implementation of this port,
with no change to the domain service above it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.servers.booking.domain.models import Booking


class BookingRepository(ABC):
    @abstractmethod
    def get_for_patient(self, patient_id: str) -> Optional[Booking]:
        """The patient's single active booking, or None."""
        ...

    @abstractmethod
    def save(self, booking: Booking) -> None:
        """Create or overwrite the patient's booking."""
        ...

    @abstractmethod
    def delete_for_patient(self, patient_id: str) -> None:
        """Remove the patient's booking (cancel)."""
        ...

    @abstractmethod
    def list_all(self) -> List[Booking]:
        """All bookings (for the scheduler scan)."""
        ...
