"""Verification domain service — phone match + identity verification."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.servers.verification.domain.models import VerificationResult
from src.servers.verification.domain.repository.interfaces import IdentityRepository
from src.shared.security import AccessContext


class VerificationService(ABC):
    @abstractmethod
    def lookup_by_phone(self, ctx: AccessContext, phone: str) -> Optional[str]:
        """Provisional match → sets ctx.matched_subject_id, returns subject id or None."""
        ...

    @abstractmethod
    def verify(self, ctx: AccessContext, name: str, dob: str) -> VerificationResult:
        """Match name+dob against the provisionally-matched record; promote ctx if ok."""
        ...


class DefaultVerificationService(VerificationService):
    def __init__(self, repository: IdentityRepository) -> None:
        self._repo = repository

    def lookup_by_phone(self, ctx: AccessContext, phone: str) -> Optional[str]:
        record = self._repo.find_by_phone(phone)
        if not record:
            return None
        ctx.matched_subject_id = record.subject_id     # provisional, NOT access
        return record.subject_id
    
    def find_record(self, subject_id: str):
        """Provisionally-matched record (used only to learn the caller's ROLE, so we
        can ask for the factors that role has). Not an authorisation check."""
        return self._repo.find_by_id(subject_id) if subject_id else None

    def verify(self, ctx: AccessContext, name: str, dob: str = "",
               clinic: str = "") -> VerificationResult:
        """Role-driven verification. Patients have a DOB on record and use
        name + DOB. Doctors have NO DOB, so their second factor is the CLINIC they
        state, and the call must come from their registered number (possession is
        what actually carries the assurance — name and clinic are semi-public).

        Two-phase: the inbound phone match tells us the candidate's ROLE, which
        decides which factors to ask for. `expect` tells the host what to ask next.

        The clinic the caller states is ONLY used for this check — scoping always
        uses the record's own clinic_id, never the asserted value."""
        record = (self._repo.find_by_id(ctx.matched_subject_id)
                  if ctx.matched_subject_id else None)

        # ── phone matched: verify with the factors this role has ──
        if record:
            if not record.matches_name(name):
                return VerificationResult(False, reason="details do not match")

            if record.role == "doctor":
                if not clinic:
                    # name is confirmed; now ask the doctor's second factor
                    return VerificationResult(False, expect="clinic",
                                              reason="need the clinic name")
                if not record.matches_clinic(clinic, self._clinic_name(record.clinic_id)):
                    return VerificationResult(False, reason="details do not match")
            else:
                if not dob:
                    return VerificationResult(False, expect="dob",
                                              reason="need the date of birth")
                if not record.matches(name, dob):
                    return VerificationResult(False, reason="details do not match")

            ctx.grant(record.subject_id, role=record.role, clinic_id=record.clinic_id)
            return VerificationResult(True, subject_id=record.subject_id,
                                      role=record.role, clinic_id=record.clinic_id)

        # ── no phone match: the caller has NO possession factor, so this is the
        #    weakest path in the system. Patients may still self-verify, but they
        #    must supply a THIRD factor (clinic) on top of name + DOB. ──
        if dob:
            record = self._repo.find_by_name_dob(name, dob)
            if record and record.role != "doctor":
                if not clinic:
                    return VerificationResult(False, expect="clinic",
                                              reason="need the clinic for an "
                                                     "unrecognised number")
                if not record.matches_clinic(clinic, self._clinic_name(record.clinic_id)):
                    return VerificationResult(False, reason="details do not match")
                ctx.grant(record.subject_id, role=record.role, clinic_id=record.clinic_id)
                return VerificationResult(True, subject_id=record.subject_id,
                                          role=record.role, clinic_id=record.clinic_id)
 
        # A doctor off their registered number has no second factor to fall back on
        # (no DOB on record), so there is no self-service path — hand to a human.
        if clinic and not dob:
            return VerificationResult(False, connect_human=True,
                                      reason="doctor calling from an unregistered number")
 
        return VerificationResult(False, reason="details do not match")

    @staticmethod
    def _clinic_name(clinic_id: str | None) -> str:
        from src.shared import database as db
        return db.clinic_name(clinic_id) if clinic_id else ""
