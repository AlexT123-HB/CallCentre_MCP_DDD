"""
Generic slot-filling — accumulate a tool's arguments across conversation turns.

A SlotState holds the partial args for one (domain, tool) and reports which required arg is still
missing, so the host can ask for it. Validation of individual values (e.g. dob
format) stays in the domain's value objects / tool; this only tracks presence.
"""

from __future__ import annotations

from typing import Dict, List, Optional


def _is_real(value) -> bool:
    """A slot is filled only if it's a non-empty, non-placeholder value."""
    if value is None:
        return False
    if isinstance(value, str):
        v = value.strip().lower()
        return v not in ("", "null", "none", "first last", "string")
    return True


class SlotState:
    """Accumulates args for one tool across turns."""

    def __init__(self, domain: str, tool: str) -> None:
        self.domain = domain
        self.tool = tool
        self.values: Dict[str, object] = {}

    def update(self, extracted: Dict[str, object]) -> None:
        """Merge newly-extracted args; only real values overwrite, so a later turn
        that omits an already-filled slot doesn't wipe it."""
        for k, v in (extracted or {}).items():
            if _is_real(v):
                self.values[k] = v

    def missing(self, required: List[str]) -> Optional[str]:
        """First required arg not yet filled, or None if all present."""
        for arg in required:
            if not _is_real(self.values.get(arg)):
                return arg
        return None

    def complete(self, required: List[str]) -> bool:
        return self.missing(required) is None

    def reset(self) -> None:
        self.values = {}
