"""
Real MCP server — BOOKING domain (port 3006).

Tools:
  request_callback(call_id, verified, subject_id, role, date, time, reason)     → validate date/time + hours, read back for confirm
  submit_callback(call_id, verified, subject_id, role, date, time, reason)      → [host-invoked] book it on a "yes" (writes the record + handoff row)
  reschedule_callback(call_id, verified, subject_id, role, date, time)          → move an existing booking to a new time
  submit_reschedule(call_id, verified, subject_id, role, date, time)            → [host-invoked] apply the reschedule on a "yes"
  cancel_callback(call_id, verified, subject_id, role)                          → cancel the caller's active booking
  get_my_callback(call_id, verified, subject_id, role)                          → read: "when's my call booked?"


Self-scoped: every operation is on the VERIFIED CALLER'S OWN booking, keyed by their subject_id. 
A booking is authoritative here (Redis); the Excel handoff sheet is a derived read-only view for staff.

Run:
    BOOKING_PORT=3006 python -m src.servers.booking.run
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from src.servers.booking.infrastructure.application import build_booking_service
from src.shared.security import AccessContext, AccessDenied
from src.shared.logging_config import setup_logging
from src.shared.cache import get_cache
from src.shared import database as db

setup_logging()          # this server's logs → the shared heartbug.log
mcp = FastMCP("heartbug-booking")
log = logging.getLogger("heartbug")
_service = build_booking_service()
_cache = get_cache()


def _self(verified: bool, subject_id: str | None):
    """A booking op needs a verified caller with a subject_id. -> (ok, err_or_None)."""
    if not verified or not subject_id:
        return False, {"found": False, "reason": "not verified"}
    return True, None

@mcp.tool()
def request_callback(call_id: str, verified: bool, subject_id: str | None,
                     role: str = "patient", date: str = "", time: str = "", reason: str = "") -> dict:
    """Request a staff CALLBACK at a specific date and time. `date` is YYYY-MM-DD,
    `time` is 24-hour HH:MM — resolve the caller's words using today's date.
 
    `reason` is a SHORT, ACCURATE note for the staff member who will take the call —
    what the caller actually needs, grounded in what happened THIS conversation:
    - Capture the PROBLEM, not the scheduling (never "caller wants a callback at 9am").
    - If the caller's request COULDN'T be completed by the assistant (it was outside scope, blocked, 
      or not something the system can do), say so plainly — 
      e.g. "asked to view another patient's record (not permitted) — wants to speak to someone", 
      "out-of-scope question about pepper pricing". Do NOT reword such a request as if it were the caller's 
      own routine concern (e.g. do NOT write "patient monitoring status" when they were asking about SOMEONE ELSE's record).
    - If the caller gave no concern at all (just asked for a person), leave `reason` empty — don't invent one.
    Base it only on what the caller actually said/did this call; never guess a topic.
 
    Previews and reads back for confirmation; does NOT book (host books on a 'yes')."""
    ok, err = _self(verified, subject_id)
    if not ok:
        return err
    return _service.preview_booking(subject_id, (date or "").strip(), (time or "").strip(), reason)

@mcp.tool()
def submit_callback(call_id: str, verified: bool, subject_id: str | None,
                    role: str = "patient", date: str = "", time: str = "", reason: str = "") -> dict:
    """[host-invoked only] Book a previewed callback after the caller confirms. The
    host supplies the args (date, time, reason) from the confirmed preview; for an
    escalation callback the host passes the reason it stored at escalation time, so the
    booked record's reason is stable from the first offer through to the saved booking.
    Not planner-selectable."""
    ok, err = _self(verified, subject_id)
    if not ok:
        return err
    return _service.confirm_booking(subject_id, (date or "").strip(),
                                    (time or "").strip(), reason, call_id)

@mcp.tool()
def reschedule_callback(call_id: str, verified: bool, subject_id: str | None,
                        role: str = "patient", date: str = "", time: str = "") -> dict:
    """Reschedule an existing callback with a NEW date/time. Preview for confirmation. Use when a
    caller wants to move their own callback."""
    ok, err = _self(verified, subject_id)
    if not ok:
        return err
    return _service.preview_reschedule(subject_id, (date or "").strip(),
                                      (time or "").strip())

@mcp.tool()
def submit_reschedule(call_id: str, verified: bool, subject_id: str | None,
                      role: str = "patient", date: str = "", time: str = "") -> dict:
    """[host-invoked only] Submit a previewed reschedule after the caller has confirmed. Not selected by the planner."""
    ok, err = _self(verified, subject_id)
    if not ok:
        return err
    return _service.confirm_reschedule(subject_id, (date or "").strip(),
                                       (time or "").strip(), call_id)

@mcp.tool()
def cancel_callback(call_id: str, verified: bool, subject_id: str | None,
                    role: str = "patient") -> dict:
    """Cancel the verified caller's own callback. Use when a caller wants to cancel their own callback."""
    ok, err = _self(verified, subject_id)
    if not ok:
        return err
    return _service.cancel_booking(subject_id, call_id)

@mcp.tool()
def get_my_callback(call_id: str, verified: bool, subject_id: str | None,
                    role: str = "patient") -> dict:
    """Get the verified caller's own callback details. Use when a caller wants to know when their callback is booked."""
    ok, err = _self(verified, subject_id)
    if not ok:
        return err
    return _service.get_booking(subject_id)

if __name__ == "__main__":
    port = int(os.getenv("BOOKING_PORT", "3006"))
    mcp.settings.host = os.getenv("MCP_BIND_HOST", "127.0.0.1")
    # internal Docker network: services call each other by container name, which the
    # default DNS-rebinding guard (localhost-only) would reject with 421. Disable it
    # here; host validation belongs at the external gateway, not between services.
    from mcp.server.transport_security import TransportSecuritySettings
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=(os.getenv("MCP_DNS_REBIND_PROTECT", "0") == "1"))
    mcp.settings.port = port
    mcp.run(transport="streamable-http")