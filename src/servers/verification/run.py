"""
Real MCP server — VERIFICATION domain (production transport).

Runs as its own process exposing the verification tools over MCP (HTTP/SSE).
This is a true bounded context at the protocol level: this server has no way to
reach the patient server's tools — isolation is enforced by the protocol, not by
discipline.

Run:
    python -m src.servers.verification.run        # serves on :3001

The AccessContext does NOT cross the wire as an object. Instead, identity-bearing
parameters travel with each call, and the server reconstructs/enforces them. For
verification specifically, the tools take the call's identity primitives directly.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from src.servers.verification.infrastructure.application import build_verification_service
from src.shared.security import AccessContext

mcp = FastMCP("heartbug-verification")
_service = build_verification_service()

@mcp.tool()
def lookup_by_phone(call_id: str, phone: str) -> dict:
    """Provisional match of an inbound caller by phone number (not verification)."""
    # verification is pre-identity, so a fresh context is fine here
    ctx = AccessContext(call_id=call_id)
    sid = _service.lookup_by_phone(ctx, phone)
    record = _service.find_record(sid) if sid else None
    return {"matched": sid is not None, "subject_id": sid,
            "matched_subject_id": ctx.matched_subject_id,
            "role": record.role if record else None}


@mcp.tool()
def verify_identity(call_id: str, matched_subject_id: str | None,
                    name: str, dob: str = "", clinic: str = "") -> dict:
    """Verify the caller's identity. Always needs their full NAME. The second factor
    depends on who they are: PATIENTS give their date of birth; DOCTORS have no date
    of birth on file, so they give their CLINIC name instead (and must be calling
    from their registered number). Give whichever the caller supplies — if you don't
    know yet which is needed, send just the name and the reply will say what to ask
    for next."""
    ctx = AccessContext(call_id=call_id, matched_subject_id=matched_subject_id)
    r = _service.verify(ctx, name, dob, clinic)
    return {"verified": r.verified, "subject_id": r.subject_id,
            "role": r.role, "clinic_id": r.clinic_id, "reason": r.reason,
            "connect_human": r.connect_human, "expect": r.expect}


if __name__ == "__main__":
    port = int(os.getenv("VERIFICATION_PORT", "3001"))
    #mcp.settings.host = os.getenv("MCP_BIND_HOST")
    mcp.settings.host = os.getenv("MCP_BIND_HOST", "127.0.0.1")
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
    
