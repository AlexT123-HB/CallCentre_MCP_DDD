"""
Real MCP server — DOCTOR domain (production transport).

Runs as its own process. The verified CALLER is a doctor (role=doctor). These
tools take a patient's FULL NAME (as spoken on the call) and resolve it to a
patient under the doctor's care, then return monitoring status / alerts, or send
the report email.

Name resolution & disambiguation (affects turns):
  - 1 match  → proceed
  - 0 match  → "no patient by that name under your care"
  - many     → return the candidates as a clarification; the host speaks it and the
               doctor picks (a follow-up turn). We keep it simple: ask them to
               confirm which patient (by a distinguishing detail).

Authorization: the care relationship is enforced by the PATIENT domain (A1). The
doctor→patient call carries the service token (set via HEARTBUG_SERVICE_TOKEN),
which the patient OHS validates.

Run:
    python -m src.servers.doctor.run            # serves on :3005
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from src.servers.doctor.infrastructure.application import build_doctor_service
from src.shared.security import AccessContext, AccessDenied

mcp = FastMCP("heartbug-doctor")
_service = build_doctor_service()


def _context(call_id: str, verified: bool, subject_id: str | None, role: str) -> AccessContext:
    ctx = AccessContext(call_id=call_id)
    if verified and subject_id:
        ctx.grant(subject_id, role=role)
    return ctx


async def _resolve_or_clarify(ctx: AccessContext, patient_name: str):
    """Resolve a spoken name to a single patient under care, or return a dict
    describing why we can't yet (no match / needs disambiguation)."""
    matches = await _service.resolve_patient(ctx, patient_name)
    if not matches:
        return None, {"found": False, "event": "doctor_no_patient",
                      "patient_name": patient_name}
    if len(matches) > 1:
        return None, {"found": False, "event": "doctor_disambiguate",
                      "patient_name": patient_name,
                      "candidates": [m.name for m in matches]}
    return matches[0], None


@mcp.tool()
async def check_patient_status(call_id: str, verified: bool, subject_id: str | None,
                               role: str = "doctor", patient_name: str = "") -> dict:
    """Check the monitoring status of a patient UNDER THE CALLING DOCTOR'S CARE —
    their enrolment status, how long is left in the monitoring window, and whether
    the clinical report is ready. Use for a doctor asking things like "how is my
    patient <name> going", "is <name> still being monitored", "when does <name>
    finish", or "is <name>'s report ready". Takes the patient's full name."""
    ctx = _context(call_id, verified, subject_id, role)
    if role != "doctor":
        return {"found": False, "event": "doctor_role_required"}
    try:
        patient, clarify = await _resolve_or_clarify(ctx, patient_name)
        if clarify:
            return clarify
        sp = await _service.patient_status(ctx, patient.patient_id)
    except AccessDenied as e:
        return {"found": False, "reason": str(e)}
    if not sp:
        return {"found": False, "event": "doctor_no_patient", "patient_name": patient_name}
    return {"found": True, "event": "doctor_patient_status", "name": sp.name,
            "enrolment_status": sp.enrolment_status, "monitoring_end": sp.monitoring_end,
            "report_ready": sp.report_ready}


@mcp.tool()
async def check_patient_alerts(call_id: str, verified: bool, subject_id: str | None,
                               role: str = "doctor", patient_name: str = "") -> dict:
    """Check clinical alerts raised for a patient UNDER THE CALLING DOCTOR'S CARE.
    Use for "are there any alerts for <name>", "has <name> had any events", "any
    issues with <name>". Takes the patient's full name; reports the alerts or that
    none were raised."""
    ctx = _context(call_id, verified, subject_id, role)
    if role != "doctor":
        return {"found": False, "event": "doctor_role_required"}
    try:
        patient, clarify = await _resolve_or_clarify(ctx, patient_name)
        if clarify:
            return clarify
        alerts = await _service.patient_alerts(ctx, patient.patient_id)
    except AccessDenied as e:
        return {"found": False, "reason": str(e)}
    return {"found": True, "event": "doctor_patient_alerts", "name": patient.name,
            "alert_count": len(alerts),
            "alerts": [{"severity": a.severity, "kind": a.kind,
                        "raised_at": a.raised_at, "note": a.note} for a in alerts]}


@mcp.tool()
async def send_patient_report(call_id: str, verified: bool, subject_id: str | None,
                              role: str = "doctor", patient_name: str = "") -> dict:
    """Email a patient's clinical report to the patient's REGISTERED address, if the
    report is ready. Use when a doctor asks to "send <name>'s report" or "email me
    <name>'s results". Only the registered-address send is automated; anything
    unusual is escalated to a human."""
    ctx = _context(call_id, verified, subject_id, role)
    if role != "doctor":
        return {"found": False, "event": "doctor_role_required"}
    try:
        patient, clarify = await _resolve_or_clarify(ctx, patient_name)
        if clarify:
            return clarify
        dispatch = await _service.send_report(ctx, patient.patient_id)
    except AccessDenied as e:
        return {"found": False, "reason": str(e)}
    if dispatch.needs_human:
        return {"found": True, "event": "doctor_report_escalate", "name": patient.name,
                "reason": dispatch.reason}
    return {"found": True, "event": "doctor_report_sent" if dispatch.sent
            else "doctor_report_unavailable", "name": patient.name,
            "reason": dispatch.reason}


if __name__ == "__main__":
    port = int(os.getenv("DOCTOR_PORT", "3005"))
    mcp.settings.host = os.getenv("MCP_BIND_HOST", "127.0.0.1")
    #mcp.settings.host = os.getenv("MCP_BIND_HOST")
    mcp.settings.port = port
    from mcp.server.transport_security import TransportSecuritySettings
    _allowed = os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",")
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed,
        allowed_origins=[f"http://{h}" for h in _allowed])
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        print("\nShutting down server...")
