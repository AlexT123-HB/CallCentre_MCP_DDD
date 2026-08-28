"""Booking service interface — the domain's business-logic contract.

Returns plain result dicts (event + fields) that the MCP tool layer narrates. The
service owns validation, lifecycle, and ownership — the tool layer is thin.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BookingService(ABC):
    @abstractmethod
    def preview_booking(self, subject_id: str, date: str, time: str,
                        reason: str = "") -> dict:
        """Validate a requested slot and return a preview (no write)."""
        ...

    @abstractmethod
    def confirm_booking(self, subject_id: str, date: str, time: str,
                        reason: str = "") -> dict:
        """Book a validated slot (write). Host-invoked on a 'yes'."""
        ...

    @abstractmethod
    def preview_reschedule(self, subject_id: str, date: str, time: str) -> dict:
        """Validate a new slot for an existing booking (no write)."""
        ...

    @abstractmethod
    def confirm_reschedule(self, subject_id: str, date: str, time: str) -> dict:
        """Apply a validated reschedule (write). Host-invoked on a 'yes'."""
        ...

    @abstractmethod
    def cancel_booking(self, subject_id: str) -> dict:
        """Cancel the caller's booking."""
        ...

    @abstractmethod
    def get_booking(self, subject_id: str) -> dict:
        """Read the caller's current booking."""
        ...
