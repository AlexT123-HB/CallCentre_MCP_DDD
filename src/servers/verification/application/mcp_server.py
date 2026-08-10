"""Verification MCP application service — the gated domain reachable when unverified."""

from __future__ import annotations

from typing import Callable, List, Tuple

from src.servers.verification.domain.service.services import VerificationService
from src.shared.application_base import MCPApplicationService
from src.shared.security import AccessContext


class VerificationMCPService(MCPApplicationService):
    domain_name = "verification"

    def __init__(self, service: VerificationService, mcp=None) -> None:
        self._service = service
        super().__init__(mcp)

    @property
    def tools(self) -> List[Tuple[str, str, Callable]]:
        return [
            ("lookup_by_phone",
             "Provisional match of an inbound caller by phone number (not verification).",
             self.lookup_by_phone),
            ("verify_identity",
             "Verify the caller's identity using their full name and date of birth.",
             self.verify_identity),
        ]

    def lookup_by_phone(self, ctx: AccessContext, phone: str) -> dict:
        sid = self._service.lookup_by_phone(ctx, phone)
        return {"matched": sid is not None, "subject_id": sid}

    def verify_identity(self, ctx: AccessContext, name: str, dob: str) -> dict:
        r = self._service.verify(ctx, name, dob)
        return {"verified": r.verified, "subject_id": r.subject_id,
                "role": r.role, "reason": r.reason}
