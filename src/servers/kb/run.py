"""
Real MCP server — KNOWLEDGE BASE domain (port 3004).

Tool:
  search_kb(call_id, verified, query) → an answer from the KB, or a miss

KB is SHARED knowledge (not caller-owned), so there's no subject_id / ownership.
It IS reachable before verification (general product questions), but sensitive
entries are only returned to verified callers — the service enforces that. The
result carries the narration event so the host stays generic:
  found          → "kb_answer"
  needs_verify   → "kb_needs_verify"  (a real answer exists but needs verifying)
  otherwise      → "kb_miss"

Run:
    KB_PORT=3004 python -m src.servers.kb.run
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from src.servers.kb.infrastructure.application import build_kb_service
from src.shared.logging_config import setup_logging
from src.servers.kb import semantic_cache

setup_logging()                      # so KB logs reach the shared log file
mcp = FastMCP("heartbug-kb")
_service = build_kb_service()
log = logging.getLogger("heartbug")


@mcp.tool()
def search_kb(call_id: str, query: str = "", verified: bool = False) -> dict:
    """Answer a general HeartBug product/how-to question from the knowledge base."""
    log.info(f"search_kb GOT query={query!r} verified={verified}")

    cached = semantic_cache.get(query)  # Check cache first
    if cached:
        return cached
    
    result = _service.answer(query, verified)
    if result.found and result.entries:
        answer = {"found": True, "event": "kb_answers",
                  "answers": [{"question": e.question, "short_answer": e.short_answer}
                              for e in result.entries],         # ← list of up to 5
                  "score": round(result.score, 3)}
        semantic_cache.put(query, answer)  # Store in cache
        return answer
    if result.needs_verification:
        return {"found": False, "event": "kb_needs_verify", "score": round(result.score, 3)}
    return {"found": False, "event": "kb_miss", "score": round(result.score, 3)}


if __name__ == "__main__":
    port = int(os.getenv("KB_PORT", "3004"))
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