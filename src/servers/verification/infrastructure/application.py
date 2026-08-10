"""Verification infrastructure — concrete identity repository + wiring.

Lookups span EVERY caller-identity source declared in a domain manifest (patients,
doctors, …), resolved against the shared database by name. Adding a new caller type
is a manifest declaration — no edits here.
"""

from __future__ import annotations

from typing import Optional

from src.servers.manifests import identity_sources
from src.servers.verification.domain.repository.interfaces import (
    IdentityRepository, IdentityRecord)
from src.servers.verification.domain.service.services import DefaultVerificationService
from src.shared import database as db


def _record(source: dict, row: dict) -> IdentityRecord:
    """Build an IdentityRecord from a row using the source's declared id field/role."""
    return IdentityRecord(subject_id=row[source["id_field"]], name=row["name"],
                          dob=row["dob"], role=source["role"], clinic_id=row.get("clinic_id"))


class IdentityRepositoryImpl(IdentityRepository):
    """Reads identity across all manifest-declared sources (patients, doctors, …)."""

    def find_by_phone(self, phone: str) -> Optional[IdentityRecord]:
        for source in identity_sources():
            phone_index = getattr(db, source["phone_index"], {})
            sid = phone_index.get(phone)
            if sid:
                table = getattr(db, source["table"], {})
                row = table.get(sid)
                if row:
                    return _record(source, row)
        return None

    def find_by_id(self, subject_id: str) -> Optional[IdentityRecord]:
        for source in identity_sources():
            table = getattr(db, source["table"], {})
            row = table.get(subject_id)
            if row:
                return _record(source, row)
        return None

    def find_by_name_dob(self, name: str, dob: str) -> Optional[IdentityRecord]:
        if not name or not dob:
            return None
        for source in identity_sources():
            table = getattr(db, source["table"], {})
            for row in table.values():
                rec = _record(source, row)
                if rec.matches(name, dob):
                    return rec
        return None


def build_verification_service() -> DefaultVerificationService:
    return DefaultVerificationService(IdentityRepositoryImpl())