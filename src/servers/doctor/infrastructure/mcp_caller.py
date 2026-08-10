"""
Doctor infrastructure — a minimal MCP caller used by the ACL to reach the PATIENT
domain's OHS over the wire.

This is the transport the ACL sits on top of. It's deliberately tiny: connect to a
domain's MCP URL, call one tool, return the parsed dict. The doctor domain being a
true bounded context, it reaches the patient domain ONLY through this wire call —
never by importing patient code.
"""

from __future__ import annotations

import json
import os
import logging
from typing import Any, Dict

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# where each domain server lives (the doctor only needs the patient server)
_URLS: Dict[str, str] = {
    "patient": os.getenv("PATIENT_URL", "http://localhost:3002/mcp"),
}

_log = logging.getLogger("heartbug")
class MCPCaller:
    """Sync-facing façade over an async MCP call, so the ACL (and the sync-ish
    service) can call `caller.call(domain, tool, **args)` without managing loops."""

    async def call_async(self, domain: str, tool: str, **kwargs) -> Any:
        url = _URLS.get(domain)
        if not url:
            _log.info(f"unknown domain {domain}")
            return {"ok": False, "reason": f"unknown domain {domain}"}
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, kwargs)
                _log.info(f"MCP call result: {result}")
                return self._parse(result)

    def call(self, domain: str, tool: str, **kwargs) -> Any:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.call_async(domain, tool, **kwargs))

    @staticmethod
    def _parse(result) -> Any:
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"ok": False, "reason": "unparseable response"}
        return {"ok": False, "reason": "empty response"}
