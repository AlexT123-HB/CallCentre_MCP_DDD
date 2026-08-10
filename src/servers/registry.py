"""
Domain registry (abstract factory).

Register domains by name; retrieve by name. Adding a domain = register() it; no
if/elif chain. Patient-only for now; Order registers here later.
"""

from __future__ import annotations

from typing import Dict

from src.shared.application_base import MCPApplicationService


class DomainRegistry:
    def __init__(self) -> None:
        self._domains: Dict[str, MCPApplicationService] = {}

    def register(self, app_service: MCPApplicationService) -> None:
        name = getattr(app_service, "domain_name", None)
        if not name:
            raise ValueError(f"{app_service!r} has no domain_name")
        self._domains[name] = app_service

    def get(self, name: str) -> MCPApplicationService:
        if name not in self._domains:
            raise KeyError(f"no domain '{name}' (known: {list(self._domains)})")
        return self._domains[name]

    def has(self, name: str) -> bool:
        return name in self._domains

    def names(self) -> list:
        return list(self._domains)

    def tool_index(self) -> dict:
        """tool name → domain name (lets the router resolve a tool to its domain)."""
        idx = {}
        for dname, svc in self._domains.items():
            for tname in svc.tool_names():
                idx[tname] = dname
        return idx
