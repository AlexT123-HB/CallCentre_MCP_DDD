"""
Booking handoff — writes a human-readable row per callback so a staff member
picking up the call has the context at the first moment.

Booking is authoritative IN OUR SYSTEM (the callback records). This module writes
a DERIVED, human-readable view for staff — it is NOT the source of truth. If the
sheet and our records ever disagree, our records win; the sheet is a convenience
layer for humans.

The write target is a thin seam:
  - now: appends a row to a local file (HEARTBUG_HANDOFF_PATH), which works in dev
    and can be pointed anywhere.
  - later: swap `_write_row` for a Microsoft Graph call that appends to a workbook
    table in your M365 tenant (approved-read-only for staff). The row shape below
    is exactly what the Excel columns should be, so the Graph version is a drop-in.

Best-effort by contract: the caller wraps this in try/except so a handoff-sheet
failure never fails the booking itself.
"""
from __future__ import annotations

import os
import csv
import logging
from datetime import datetime, timezone

_log = logging.getLogger("heartbug")

# where the handoff rows are written. In dev this is a local CSV; in your tenant
# this is replaced by a Microsoft Graph workbook write (see _write_row).
_HANDOFF_PATH = os.getenv("HEARTBUG_HANDOFF_PATH", "/app/logs/callback_handoff.csv")

# the human-readable columns a staffer sees, in order. Keep this list in sync with
# the Excel sheet's header row.
_COLUMNS = [
    "booking_id",       # CB-XXXXXX — the ref the patient can quote
    "scheduled_for",    # ISO datetime the call is booked for
    "window_min",       # call may vary +/- this many minutes
    "name",             # patient name (verified)
    "patient_id",       # internal id (staff lookup)
    "reason",           # why they want the call (their words)
    "status",           # pending / due / done
    "booked_at",        # when the booking was made
    "call_id",          # the conversation this came from (for context lookup)
]

def _row_from_record(record: dict, call_id: str) -> dict:
    """Assemble the human-readable handoff row from a booking record.
    The `booked_at` column shows the RESCHEDULE time when the booking was moved,
    else the original booking time — so staff always see when it was last set."""
    booked_at = record.get("rescheduled_at") or record.get("requested_at") \
        or datetime.now().isoformat()
    return {
        "booking_id":    record.get("booking_id", ""),
        "scheduled_for": record.get("scheduled_for", ""),
        "window_min":    record.get("window_min", ""),
        "name":          record.get("name", ""),
        "patient_id":    record.get("patient_id", ""),
        "reason":        record.get("reason", "") or "(not given)",
        "status":        record.get("status", "pending"),
        "booked_at":     booked_at,
        "call_id":       call_id or "",
    }


def _write_row(row: dict) -> None:
    """Upsert one row into the handoff sheet, keyed by booking_id: if a row with
    this booking_id exists, OVERWRITE it (so a reschedule updates the current row
    rather than appending a stale duplicate); otherwise append. Staff always see
    ONE current row per booking.

    DEV: local CSV (rewrites the file with the row replaced/added).
    PROD (plug point): replace with a Microsoft Graph call — find the table row by
      booking_id and PATCH it, or POST a new row if absent. The row dict is already
      shaped to the columns, so only the transport changes.
    """
    os.makedirs(os.path.dirname(_HANDOFF_PATH), exist_ok=True)
    rows: list[dict] = []
    if os.path.exists(_HANDOFF_PATH):
        with open(_HANDOFF_PATH, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    # replace the row with this booking_id, or append if new
    replaced = False
    for i, existing in enumerate(rows):
        if existing.get("booking_id") == row["booking_id"]:
            rows[i] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)

    with open(_HANDOFF_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_booking_row(record: dict, call_id: str = "") -> None:
    """Public entry: write a booking's handoff row. Best-effort — the caller
    guards this so a failure here never fails the booking."""
    row = _row_from_record(record, call_id)
    _write_row(row)
    _log.info(f"[{call_id}] HANDOFF row written: {row['booking_id']} "
              f"{row['name']} @ {row['scheduled_for']}")
