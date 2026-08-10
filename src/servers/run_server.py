"""
Server runner — OPTION A: ONE MCP server, all domains registered on it.

Domains now: verification (gated, reachable while unverified) + patient (data).
Order added later. For OPTION B, split into run_<domain>_service.py per domain.
"""

from __future__ import annotations

from src.servers.registry import DomainRegistry

from src.servers.verification.application.mcp_server import VerificationMCPService
from src.servers.verification.infrastructure.application import build_verification_service

from src.servers.patient.application.mcp_server import PatientMCPService
from src.servers.patient.infrastructure.application import build_patient_service
from src.shared.logging_config import setup_logging


setup_logging()

def build_registry(mcp=None) -> DomainRegistry:
    registry = DomainRegistry()

    # ── Verification domain (the gated one) ──
    registry.register(VerificationMCPService(build_verification_service(), mcp=mcp))

    # ── Patient domain (data only) ──
    registry.register(PatientMCPService(build_patient_service(), mcp=mcp))

    # ── Order domain (added later) ──
    # registry.register(OrderMCPService(build_order_service(), mcp=mcp))

    return registry


if __name__ == "__main__":
    reg = build_registry()
    print("server up (Option A). domains:", reg.names())
    print("tools:", reg.tool_index())
