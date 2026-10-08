"""
Shared access/security primitive — used by every domain.

AccessContext carries the VERIFIED CALLER's identity through the system, generic
across caller types. The caller may be a patient, doctor, or technician, so the
identity field is `subject_id` (not patient_id) — it holds whatever the verified
caller's id is, and `role` says what kind of subject it is.

Resource IDs (order_id, report_id, a patient's own patient_id) are domain-specific
and live on domain models — NOT here. This context is only about WHO is calling.

The verification domain WRITES it (on success); all other domains READ it to gate
access. Separates provisional phone-match from authoritative post-verification id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class AccessDenied(Exception):
    """Raised when an operation is attempted without the required verified identity."""


@dataclass
class AccessContext:
    call_id: str
    matched_subject_id: Optional[str] = None   # from phone lookup — provisional, NOT access
    verified: bool = False
    subject_id: Optional[str] = None           # authoritative caller id — set ONLY after verification
    role: str = "patient"                       # patient / doctor / technician / ...
    clinic_id: Optional[str] = None             # the caller's clinic (1→1) — set at verification
    reason_of_escalation: Optional[str] = None           # if the caller was escalated, why

    def require_verified(self) -> None:
        if not self.verified or not self.subject_id:
            raise AccessDenied("caller is not verified")

    def owns(self, owner_id: str) -> bool:
        """Self-ownership: is this resource the caller's own? (subject_id matches the
        resource's owner id). Doctor/delegated access uses a relationship check in
        its own domain, NOT this helper."""
        return self.verified and self.subject_id == owner_id

    def grant(self, subject_id: str, role: str = "patient", clinic_id: Optional[str] = None) -> None:
        """Promote to verified (called by the verification domain only)."""
        self.verified = True
        self.subject_id = subject_id
        self.role = role
        self.clinic_id = clinic_id

    def is_service(self) -> bool:
        """A privileged service-to-service identity (one domain calling another on
        a verified caller's behalf), NOT an end caller. Set by grant(role=service)."""
        return self.verified and self.role == "service"
