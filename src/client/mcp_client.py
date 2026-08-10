"""
Host-side REAL MCP client — connects to each domain server over the wire.

This is the production transport: a domain → server-URL map, one MCP session per
server. Replaces the in-process registry. The gate is still enforced here (the
host won't even call a server the caller isn't allowed to reach), and the verified
identity travels as call parameters (since AccessContext can't cross the wire as
an object). The server RE-ENFORCES authorization from those params.

Each call opens a short-lived MCP session. For high throughput you'd pool/keep
sessions warm; this version favors correctness and isolation first.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, streamablehttp_client

from src.client.gate import available_domains
from src.servers.manifests import server_urls
from src.shared.security import AccessContext
from src.shared import tracing

# domain → server URL, derived from domain manifests (not hardcoded)
DOMAIN_SERVERS: Dict[str, str] = server_urls()


class MCPClient:
    """Connects the host to domain MCP servers over the wire, enforcing the gate."""

    def __init__(self, registry=None) -> None:
        # registry kept only so the gate can enumerate domain names
        self._registry = registry
        self._tool_cache: Dict[str, list] = {}        # domain → discovered tools (cached)

    async def discover_tools(self, domain: str) -> list:
        """List a domain server's tools via MCP (cached per domain for the session)."""
        if domain in self._tool_cache:
            return self._tool_cache[domain]
        url = DOMAIN_SERVERS.get(domain)
        if not url:
            return []
        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as mcp_session:
                await mcp_session.initialize()
                result = await mcp_session.list_tools()
                tools = list(getattr(result, "tools", []) or [])
        self._tool_cache[domain] = tools
        return tools

    async def call(self, session: dict, domain: str, tool: str, **kwargs) -> Any:
        # ── gate: is this domain reachable in the current verify state? ──
        allowed = available_domains(session, self._registry)
        if domain not in allowed:
            return {"error": "domain_not_available",
                    "reason": f"'{domain}' not reachable until verified"}

        url = DOMAIN_SERVERS.get(domain)
        if not url:
            return {"error": "unknown_domain", "reason": f"no server for '{domain}'"}

        # ── AccessContext → call parameters (it can't cross the wire as an
        #    object). The server re-enforces authorization from these. ──
        ctx: AccessContext = session["ctx"]
        #args = {**self._identity_params(ctx), **kwargs}
        identity = self._identity_params(ctx)
        safe_kwargs = {k: v for k, v in kwargs.items() if v is not None and k not in identity}
        args = {**safe_kwargs, **identity}

        async with streamable_http_client(url) as (read, write, _):
            async with ClientSession(read, write) as mcp_session:
                # await mcp_session.initialize()
                # result = await mcp_session.call_tool(tool, args)
                # return self._unwrap(result)
                await mcp_session.initialize()
                # The single most useful span in the system: it shows, side by side,
                # what the LLM asked for, what identity the host injected, and what
                # the domain returned. Most bugs live in that comparison.
                with tracing.span(f"{domain}.{tool}", tracing.TOOL,
                                  domain=domain, tool=tool,
                                  args_from_llm=kwargs,
                                  args_sent=args,
                                  identity=identity) as s:
                    result = await mcp_session.call_tool(tool, args)
                    out = self._unwrap(result)
                    s.set(result=out,
                          event=(out or {}).get("event") if isinstance(out, dict) else None,
                          found=(out or {}).get("found") if isinstance(out, dict) else None)
                    return out

    @staticmethod
    def _identity_params(ctx: AccessContext) -> Dict[str, Any]:
        return {
            "call_id": ctx.call_id,
            "verified": ctx.verified,
            "subject_id": ctx.subject_id,
            "role": ctx.role,
            "clinic_id": ctx.clinic_id,
            "matched_subject_id": ctx.matched_subject_id,
        }

    @staticmethod
    def _unwrap(result) -> Any:
        if getattr(result, "structuredContent", None):
            return result.structuredContent
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"result": text}
        return {}