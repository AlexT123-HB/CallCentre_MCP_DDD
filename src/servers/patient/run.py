"""
Real MCP server — PATIENT domain (production transport).

Runs as its own process. Patient data is gated: the tool requires a VERIFIED
identity, passed as parameters (the host vouches for verification). The server
re-enforces the rule — it never returns data without verified=True and a
subject_id, regardless of what the host claims.

Run:
    python -m src.servers.patient.run            # serves on :3002

Security note: passing verified+subject_id over the wire means the server trusts
the host's assertion. In production this link MUST be authenticated (signed token
/ mTLS) so a rogue caller can't forge "verified=true". See app-layer auth (Tier-1
item 3). The structure here isolates exactly where that trust boundary sits.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from src.servers.patient.infrastructure.application import build_patient_service
from src.shared.security import AccessContext, AccessDenied
from src.shared.logging_config import setup_logging
from src.shared import database as db

mcp = FastMCP("heartbug-patient")
_service = build_patient_service()
setup_logging()


def _context(call_id: str, verified: bool, subject_id: str | None, role: str) -> AccessContext:
    """Rebuild the access context from the identity params the host passed."""
    ctx = AccessContext(call_id=call_id)
    if verified and subject_id:
        ctx.grant(subject_id, role=role)
    return ctx

def _service_context(call_id: str, service_token: str) -> AccessContext:
    """Rebuild the access context for a service-to-service call (doctor → patient)."""
    ctx = AccessContext(call_id=call_id)
    if db.is_valid_service_token(service_token):
        ctx.grant("__service__", role=db.SERVICE_ROLE)  # service identity (doctor domain) — not a real user
    return ctx

@mcp.tool()
def get_my_info(call_id: str, verified: bool, subject_id: str | None,
                role: str = "patient") -> dict:
    """Return the verified caller's own patient record."""
    ctx = _context(call_id, verified, subject_id, role)
    try:
        patient = _service.find_self(ctx)          # raises if not verified
    except AccessDenied as e:
        return {"found": False, "reason": str(e)}
    if not patient:
        return {"found": False, "reason": "patient not found"}
    return {"found": True, "name": patient.name, "dob": patient.dob}


@mcp.tool()
def prewarm(call_id: str, verified: bool, subject_id: str | None,
            role: str = "patient") -> dict:
    """
    Warm this caller's patient data into the server cache BEFORE they ask, so the
    first real request is a cache hit. The host calls this right after verification
    (or while the caller is still in the queue). Reads through the repository, which
    backfills the shared cache as a side effect.
    """
    ctx = _context(call_id, verified, subject_id, role)
    try:
        _service.find_self(ctx)                    # populates the cache via the repo
        return {"warmed": True, "subject_id": subject_id}
    except AccessDenied as e:
        return {"warmed": False, "reason": str(e)}

# ── clinician-facing OHS tools (consumed by the doctor domain, service-to-service) ──
# These are NOT caller-facing. They require a valid service token AND enforce the
# care relationship inside this domain (A1). The doctor domain reaches these over
# MCP and translates the results into its own model via its ACL.

@mcp.tool()
def resolve_patient_by_name(call_id: str, service_token: str, doctor_id: str,
                            full_name: str) -> dict:
    """[service] Resolve a patient's full name to id(s) UNDER THIS DOCTOR'S CARE.
    Returns 0, 1, or many matches so the caller can disambiguate."""
    ctx = _service_context(call_id, service_token)
    try:
        ids = _service.resolve_by_name_for_clinician(ctx, doctor_id, full_name)
    except AccessDenied as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "patient_ids": ids,
            "matches": [{"patient_id": pid, "name": (db.PATIENTS.get(pid) or {}).get("name")}
                        for pid in ids]}


@mcp.tool()
def get_patient_monitoring(call_id: str, service_token: str, doctor_id: str,
                           patient_id: str) -> dict:
    """[service] A patient's monitoring status (enrolment, window end, report
    readiness), gated on the care relationship."""
    ctx = _service_context(call_id, service_token)
    try:
        m = _service.monitoring_for_clinician(ctx, doctor_id, patient_id)
    except AccessDenied as e:
        return {"ok": False, "reason": str(e)}
    if not m:
        return {"ok": True, "found": False}
    return {"ok": True, "found": True, "patient_id": m.patient_id,
            "name": (db.PATIENTS.get(patient_id) or {}).get("name", ""),
            "enrolment_status": m.enrolment_status, "monitoring_end": m.monitoring_end,
            "report_ready": m.report_ready, "report_id": m.report_id}


@mcp.tool()
def get_patient_alerts(call_id: str, service_token: str, doctor_id: str,
                       patient_id: str) -> dict:
    """[service] A patient's clinical alerts, gated on the care relationship."""
    ctx = _service_context(call_id, service_token)
    try:
        alerts = _service.alerts_for_clinician(ctx, doctor_id, patient_id)
    except AccessDenied as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "alerts": [
        {"alert_id": a.alert_id, "severity": a.severity, "kind": a.kind,
         "raised_at": a.raised_at, "note": a.note} for a in alerts]}


if __name__ == "__main__":
    port = int(os.getenv("PATIENT_PORT", "3002"))
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
    
