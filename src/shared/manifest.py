"""
Domain manifest — each domain's self-description.

A manifest carries everything the host needs to know about a domain so the host
no longer hardcodes it. Adding a domain = declare a manifest + register it; the
host reads manifests and configures routing, narration, the gate, and the tool
map from them.

Security note: `requires_verification` is security-critical. The host does NOT
trust a `False` value blindly — it is validated against the central security
policy (see shared/policy.py). Mechanical fields (url, description, events,
tools) are trusted from the manifest; the security field is governed centrally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DomainManifest:
    name: str
    url: str                                   # MCP server URL
    description: str                           # for the router prompt (what it handles)
    requires_verification: bool                # gate intent (validated centrally)
    primary_tool: str                          # default read tool for a simple request
    tools: List[str] = field(default_factory=list)
    events: Dict[str, str] = field(default_factory=dict)   # narration event → instruction
    special_flow: Optional[str] = None         # e.g. "verification" for slot-filling; else generic
    identity_source: Optional[Dict[str, str]] = None   # declares this domain as a caller-identity source
    tool_events: Optional[Dict[str, str]] = None       # tool name → narration event for its success
    required_args: Optional[Dict[str, Dict[str, str]]] = None  # tool → {arg → slot-prompt event}
    confirmations: Optional[Dict[str, dict]] = None    # preview tool → {"answers": {label → {action, tool/event, hint}}}
    public: bool = False                        # reachable in BOTH states (e.g. KB) — general access
    required_role: Optional[str] = None        # if set, only callers with this role can access
