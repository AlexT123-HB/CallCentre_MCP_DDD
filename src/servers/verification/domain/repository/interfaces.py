"""Verification repository interface — the identity index (who is this caller?)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class IdentityRecord:
    """Minimal record used for matching: id + the values to verify against."""
    def __init__(self, subject_id: str, name: str, dob: str, role: str = "patient", clinic_id: Optional[str] = None) -> None:
        self.subject_id = subject_id
        self.name = name
        self.dob = dob
        self.role = role
        self.clinic_id = clinic_id

    def matches(self, name: str, dob: str) -> bool:
        return (bool(name) and bool(dob)
                and self.name.strip().lower() == name.strip().lower()
                and self.dob == dob.strip())
    
    def matches_name(self, name: str) -> bool:
        """Name only — the first factor for every role."""
        return bool(name) and self.name.strip().lower() == name.strip().lower()

    def matches_clinic(self, clinic: str, clinic_name: str) -> bool:
        """Doctors have no DOB on record, so the clinic they state is their second
        factor. Caller-friendly comparison (case/punctuation tolerant, either side
        may be a shortened form): the caller says "Melbourne Cardiac", the record
        says "Melbourne Cardiac Clinic". The asserted value is ONLY used for this
        check — scoping always uses the record's clinic_id, never the claim."""
        if not clinic or not clinic_name:
            return False
        said = _normalise_clinic(clinic)
        known = _normalise_clinic(clinic_name)
        return bool(said) and (said in known or known in said)

def _normalise_clinic(value: str) -> str:
    """Lowercase, strip punctuation and filler words, collapse whitespace."""
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in value.lower())
    drop = {"the", "clinic", "centre", "center", "practice", "medical", "hospital"}
    return " ".join(w for w in cleaned.split() if w not in drop)

class IdentityRepository(ABC):
    @abstractmethod
    def find_by_phone(self, phone: str) -> Optional[IdentityRecord]:
        """Provisional match on inbound phone → identity record (or None)."""
        ...

    @abstractmethod
    def find_by_id(self, subject_id: str) -> Optional[IdentityRecord]:
        ...

    @abstractmethod
    def find_by_name_dob(self, name: str, dob: str) -> Optional[IdentityRecord]:
        """Direct lookup by name + dob, for callers whose phone didn't match."""
        ...
