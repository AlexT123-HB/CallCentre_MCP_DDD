"""
Verification domain models — pure.

This domain owns IDENTITY VERIFICATION. The IdentityClaim value object (the
accumulating name+dob and its slot-filling rules) belongs here, because
"what makes a complete, valid identity claim" is a verification business rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class VerificationResult:
    verified: bool
    subject_id: Optional[str] = None
    role: str = "patient"
    clinic_id: Optional[str] = None
    reason: Optional[str] = None
    connect_human: bool = False       # e.g. a doctor calling from an unregistered number
    expect: Optional[str] = None    # which factor to ask for next: "dob" | "clinic"

@dataclass(frozen=True)
class IdentityClaim:
    """
    Value object: name + dob a caller provides, accumulated across turns.
    Immutable — each turn produces a NEW claim. Holds the slot-filling rules.
    """
    name: Optional[str] = None
    dob: Optional[str] = None

    _PLACEHOLDERS = frozenset({"first last", "first", "last", "name", "null", "none", ""})

    def with_name(self, name: Optional[str]) -> "IdentityClaim":
        if not name or name.strip().lower() in self._PLACEHOLDERS:
            return self
        return IdentityClaim(name=name.strip(), dob=self.dob)

    def with_dob(self, dob: Optional[str]) -> "IdentityClaim":
        if not dob or not re.match(r"^\d{4}-\d{2}-\d{2}$", dob.strip()):
            return self                       # never accept partial/fabricated date
        return IdentityClaim(name=self.name, dob=dob.strip())

    def is_complete(self) -> bool:
        return bool(self.name) and bool(self.dob)

    def missing_field(self) -> Optional[str]:
        if not self.name:
            return "name"
        if not self.dob:
            return "dob"
        return None
