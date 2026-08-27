"""Booking service — the business logic for the callback lifecycle.

Depends on the repository PORT (not the cache) and the handoff writer. Owns
validation, the preview/confirm split, lifecycle transitions, and ownership. The
MCP tool layer (run.py) is a thin pass-through to these methods.

Event names here MUST match the booking manifest's declared events.
"""
from __future__ import annotations

import logging

from src.servers.booking.domain.models import (
    Booking, parse_dt, validate_slot, WINDOW_MIN, OPEN_HOUR, CLOSE_HOUR,
)
from src.servers.booking.domain.repository.interfaces import BookingRepository
from src.servers.booking.domain.service.interfaces import BookingService
from src.shared import database as db

_log = logging.getLogger("heartbug")


class DefaultBookingService(BookingService):
    def __init__(self, repo: BookingRepository, handoff_writer=None):
        self._repo = repo
        self._handoff = handoff_writer          # callable(record_dict, call_id) or None

    # ── helpers ────────────────────────────────────────────────────────────────
    def _parse_and_validate(self, date: str, time: str):
        """→ (datetime, None) if bookable, or (None, event_dict) to return."""
        dt, perr = parse_dt(date, time)
        if perr:
            return None, {"found": True, "recorded": False, "event": "callback_need_time"}
        why = validate_slot(dt)
        if why:
            return None, {"found": True, "recorded": False, "event": "callback_out_of_hours",
                          "why": why, "open_hour": OPEN_HOUR, "close_hour": CLOSE_HOUR}
        return dt, None

    def _write_handoff(self, booking: Booking, call_id: str) -> None:
        if not self._handoff:
            return
        try:
            self._handoff(booking.to_dict(), call_id=call_id)
        except Exception as e:                  # noqa: BLE001 — never fail the booking
            _log.warning(f"[{call_id}] handoff write failed (booking saved): {e}")

    # ── book ───────────────────────────────────────────────────────────────────
    def preview_booking(self, subject_id: str, date: str, time: str,
                        reason: str = "") -> dict:
        if not date:
            return {"found": True, "recorded": False, "event": "callback_need_date"}
        if not time:
            return {"found": True, "recorded": False, "event": "callback_need_time"}
        dt, err = self._parse_and_validate(date, time)
        if err:
            return err
        return {"found": True, "needs_confirmation": True, "event": "callback_confirm",
                "scheduled_for": dt.isoformat(timespec="minutes"),
                "window_min": WINDOW_MIN, "reason": (reason or "").strip()}

    def confirm_booking(self, subject_id: str, date: str, time: str,
                        reason: str = "", call_id: str = "") -> dict:
        dt, err = self._parse_and_validate(date, time)
        if err:
            return err
        existing = self._repo.get_for_patient(subject_id)
        booking = Booking.create(
            patient_id=subject_id,
            name=_name_for(subject_id),
            scheduled_iso=dt.isoformat(timespec="minutes"),
            reason=reason,
            replaced=existing.booking_id if existing else None,
        )
        self._repo.save(booking)
        self._write_handoff(booking, call_id)
        return {"found": True, "recorded": True, "booking_id": booking.booking_id,
                "scheduled_for": booking.scheduled_for, "window_min": booking.window_min,
                "reason": booking.reason, "event": "callback_recorded"}

    # ── reschedule ─────────────────────────────────────────────────────────────
    def preview_reschedule(self, subject_id: str, date: str, time: str) -> dict:
        existing = self._repo.get_for_patient(subject_id)
        if not existing:
            return {"found": True, "recorded": False, "event": "callback_none_to_change"}
        if not date:
            return {"found": True, "recorded": False, "event": "callback_need_date"}
        if not time:
            return {"found": True, "recorded": False, "event": "callback_need_time"}
        dt, err = self._parse_and_validate(date, time)
        if err:
            return err
        return {"found": True, "needs_confirmation": True, "event": "reschedule_confirm",
                "booking_id": existing.booking_id,
                "old_scheduled_for": existing.scheduled_for,
                "scheduled_for": dt.isoformat(timespec="minutes"),
                "window_min": WINDOW_MIN}

    def confirm_reschedule(self, subject_id: str, date: str, time: str,
                           call_id: str = "") -> dict:
        existing = self._repo.get_for_patient(subject_id)
        if not existing:
            return {"found": True, "recorded": False, "event": "callback_none_to_change"}
        dt, err = self._parse_and_validate(date, time)
        if err:
            return err
        existing.reschedule_to(dt.isoformat(timespec="minutes"))
        self._repo.save(existing)
        self._write_handoff(existing, call_id)
        return {"found": True, "recorded": True, "booking_id": existing.booking_id,
                "scheduled_for": existing.scheduled_for, "window_min": existing.window_min,
                "event": "reschedule_recorded"}

    # ── cancel ─────────────────────────────────────────────────────────────────
    def cancel(self, subject_id: str, call_id: str = "") -> dict:
        existing = self._repo.get_for_patient(subject_id)
        if not existing:
            return {"found": True, "recorded": False, "event": "callback_none_to_change"}
        self._repo.delete_for_patient(subject_id)
        _log.info(f"[{call_id}] CALLBACK CANCELLED {existing.booking_id} for {subject_id}")
        return {"found": True, "recorded": True, "booking_id": existing.booking_id,
                "was_scheduled_for": existing.scheduled_for, "event": "cancel_confirmed"}

    # ── read ───────────────────────────────────────────────────────────────────
    def get(self, subject_id: str) -> dict:
        existing = self._repo.get_for_patient(subject_id)
        if not existing:
            return {"found": True, "has_booking": False, "event": "callback_none"}
        return {"found": True, "has_booking": True, "event": "callback_details",
                "booking_id": existing.booking_id, "scheduled_for": existing.scheduled_for,
                "window_min": existing.window_min, "reason": existing.reason,
                "status": existing.status}


# name lookup kept out of the aggregate (it's a cross-domain read, best-effort)
def _name_for(subject_id: str) -> str:
    try:
        return getattr(db, "PATIENTS", {}).get(subject_id, {}).get("name", subject_id)
    except Exception:                           # noqa: BLE001
        return subject_id
