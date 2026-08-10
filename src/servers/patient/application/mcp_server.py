"""
Patient MCP application service — patient DATA only (verification moved to its
own domain). Tool: get_my_info (the verified caller's own record).
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from src.servers.patient.domain.service.interfaces import PatientService
from src.shared.application_base import MCPApplicationService
from src.shared.security import AccessContext, AccessDenied


class PatientMCPService(MCPApplicationService):
    domain_name = "patient"

    def __init__(self, service: PatientService, mcp=None) -> None:
        self._service = service
        super().__init__(mcp)

    @property
    def tools(self) -> List[Tuple[str, str, Callable]]:
        return [
            ("get_my_info",
             "Return the verified caller's own patient record.",
             self.get_my_info),
        ]

    def get_my_info(self, ctx: AccessContext) -> dict:
        try:
            patient = self._service.find_self(ctx)
        except AccessDenied as e:
            return {"found": False, "reason": str(e)}
        if not patient:
            return {"found": False, "reason": "patient not found"}
        return {"found": True, "name": patient.name, "dob": patient.dob}
