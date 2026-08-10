"""
Shared base for a domain's MCP application service.

Handles tool registration so each domain only declares its tools. Works with a
real FastMCP instance (passed as `mcp`) or standalone (local registry) so the
system runs/tests without the MCP library.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List, Tuple


class MCPApplicationService(ABC):
    domain_name: str = "base"

    def __init__(self, mcp=None) -> None:
        self.mcp = mcp
        self._registry: dict = {}
        self._register_tools()

    @property
    @abstractmethod
    def tools(self) -> List[Tuple[str, str, Callable]]:
        """(tool_name, description, handler) triples."""
        ...

    def _register_tools(self) -> None:
        for name, description, handler in self.tools:
            self._registry[name] = handler
            if self.mcp is not None:
                self.mcp.tool(name=name, description=description)(handler)

    def call(self, tool_name: str, **kwargs):
        if tool_name not in self._registry:
            raise KeyError(f"{self.__class__.__name__} has no tool '{tool_name}'")
        return self._registry[tool_name](**kwargs)

    def tool_names(self) -> List[str]:
        return list(self._registry)
