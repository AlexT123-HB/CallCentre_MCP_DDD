"""Knowledge base domain — self-description."""

import os

from src.shared.manifest import DomainManifest

MANIFEST = DomainManifest(
    name="kb",
    url=os.getenv("KB_URL", "http://localhost:3004/mcp"),
    description="general HeartBug product, device, and how-to questions "
                "(recording, battery, compatibility, cleaning, support hours)",
    requires_verification=False,        # general questions need no verification
    public=True,                        # reachable in BOTH states (pre- and post-verify)
    primary_tool="search_kb",
    tools=["search_kb"],
    tool_events={"search_kb": "kb_answers"},   # default; result may override (miss/needs_verify)
    events={
        "kb_answers":       "the facts contain one or more relevant KB answers; use them to "
                            "answer the caller's question directly and concisely — synthesize "
                            "across them if several apply, don't just read all of them out",
        "kb_miss":         "say you don't have specific information on that, and offer to help "
                           "with orders, device, or account questions",
        "kb_needs_verify": "say you can answer that once they're verified, and offer to verify them",
    },
)
