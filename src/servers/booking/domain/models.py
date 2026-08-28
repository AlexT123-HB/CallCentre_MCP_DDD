"""Booking domain model — pure. A callback the caller has scheduled with staff.

References the patient by id (never embeds a Patient). Holds the lifecycle state
and the pure invariants (working hours, validity) — no storage, no transport.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── working-hours policy (pure config the domain validates against) ───────────
OPEN_HOUR  = int(os.getenv("HEARTBUG_CALLBACK_OPEN_HOUR", "9"))
CLOSE_HOUR = int(os.getenv("HEARTBUG_CALLBACK_CLOSE_HOUR", "17"))
WINDOW_MIN = int(os.getenv("HEARTBUG_CALLBACK_WINDOW_MIN", "30"))
# a callback less than this many minutes away isn't scheduled — the caller is
# connected now instead (avoids racing the scheduler's lead window).
MIN_LEAD_MIN = int(os.getenv("HEARTBUG_CALLBACK_MIN_LEAD_MIN", "30"))

# lifecycle states
PENDING   = "pending"      # booked, waiting for its time
DUE       = "due"          # within the lead window (scheduler flags this)
DONE       = "done"        # call completed


def new_booking_id() -> str:
    """Short human-quotable reference, e.g. 'CB-7F3A2C'."""
    return "CB-" + uuid.uuid4().hex[:6].upper()


def parse_dt(date_str: str, time_str: str):
    """Parse date (YYYY-MM-DD) + time (HH:MM) → (datetime, None) or (None, reason)."""
    raw = f"{(date_str or '').strip()} {(time_str or '').strip()}"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt), None
        except ValueError:
            continue
    return None, "unparseable"


def validate_slot(dt: datetime) -> Optional[str]:
    """Pure invariant check. Returns a reason if the slot is not bookable, else None:
    not in the past, not too soon (connect-now instead), a weekday, within hours."""
    now = datetime.now()
    if dt <= now:
        return "in_past"
    if dt < now + timedelta(minutes=MIN_LEAD_MIN):
        return "too_soon"                       # < 30 min out → connect now, don't schedule
    if dt.weekday() >= 5:                        # 5=Sat, 6=Sun
        return "weekend"
    if dt.hour < OPEN_HOUR or dt.hour >= CLOSE_HOUR:
        return "outside_hours"
    return None


@dataclass
class Booking:
    """A scheduled staff callback for one patient (the aggregate)."""
    booking_id: str
    patient_id: str            # reference only — never embeds a Patient
    name: str
    scheduled_for: str         # ISO datetime the call is booked for
    reason: str = ""
    window_min: int = WINDOW_MIN
    status: str = PENDING
    requested_at: str = ""
    replaced: Optional[str] = None
    rescheduled_at: Optional[str] = None
    schedule_history: list = field(default_factory=list)   # prior times, for audit

    def belongs_to(self, subject_id: str) -> bool:
        """Ownership: is this the caller's own booking?"""
        return self.patient_id == subject_id

    def reschedule_to(self, new_iso: str) -> None:
        """Move to a new time; a due/expired booking returns to pending.
        The OLD time is appended to schedule_history so the cache keeps every
        reschedule (a patient may move a booking more than once)."""
        self.schedule_history.append({
            "was_scheduled_for": self.scheduled_for,
            "moved_at": datetime.now(timezone.utc).isoformat(),
        })
        self.scheduled_for = new_iso
        self.status = PENDING
        self.rescheduled_at = datetime.now(timezone.utc).isoformat()

    @classmethod
    def create(cls, patient_id: str, name: str, scheduled_iso: str,
               reason: str = "", replaced: Optional[str] = None) -> "Booking":
        return cls(
            booking_id=new_booking_id(),
            patient_id=patient_id,
            name=name,
            scheduled_for=scheduled_iso,
            reason=(reason or "").strip(),
            window_min=WINDOW_MIN,
            status=PENDING,
            requested_at=datetime.now(timezone.utc).isoformat(),
            replaced=replaced,
        )

    def to_dict(self) -> dict:
        return {
            "booking_id": self.booking_id,
            "patient_id": self.patient_id,
            "name": self.name,
            "scheduled_for": self.scheduled_for,
            "reason": self.reason,
            "window_min": self.window_min,
            "status": self.status,
            "requested_at": self.requested_at,
            "replaced": self.replaced,
            "rescheduled_at": self.rescheduled_at,
            "schedule_history": self.schedule_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Booking":
        return cls(
            booking_id=d.get("booking_id", ""),
            patient_id=d.get("patient_id", ""),
            name=d.get("name", ""),
            scheduled_for=d.get("scheduled_for", ""),
            reason=d.get("reason", ""),
            window_min=d.get("window_min", WINDOW_MIN),
            status=d.get("status", PENDING),
            requested_at=d.get("requested_at", ""),
            replaced=d.get("replaced"),
            rescheduled_at=d.get("rescheduled_at"),
            schedule_history=d.get("schedule_history", []),
        )