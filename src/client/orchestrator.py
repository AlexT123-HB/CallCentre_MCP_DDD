"""
Host orchestrator — coordinates domain calls over real MCP (async).

Dispatches one or many domain calls; fan-out runs them concurrently with
asyncio.gather. Calls go through the async MCP client, which enforces the gate
and carries the verified identity over the wire.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.client.mcp_client import MCPClient


@dataclass
class DomainCall:
    domain: str
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregateResult:
    results: Dict[str, Any] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)


class Orchestrator:
    def __init__(self, client: MCPClient) -> None:
        self._client = client

    @staticmethod
    def key_for(domain: str, tool: str) -> str:
        """Result key. Keyed by domain AND tool because one turn can legitimately
        call several tools in the SAME domain ("how is Alex doing and any alerts?"
        is two doctor tools) — keying by domain alone silently overwrote the first
        result with the second."""
        return f"{domain}.{tool}"
 
    async def dispatch(self, session: dict, calls: List[DomainCall]) -> AggregateResult:
        async def run_one(call: DomainCall):
            key = self.key_for(call.domain, call.tool)
            try:
                res = await self._client.call(session, call.domain, call.tool, **call.args)
                return key, res, None
            except Exception as e:                       # partial-failure isolation
                return key, None, e
 
        gathered = await asyncio.gather(*(run_one(c) for c in calls))
        agg = AggregateResult()
        for key, res, err in gathered:
            if err is not None:
                agg.failures[key] = str(err)
            else:
                agg.results[key] = res
        return agg
 
