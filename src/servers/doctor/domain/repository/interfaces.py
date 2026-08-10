"""
Doctor domain — the ACL CONTRACT (ports the domain needs from the outside world).

This is the interface the doctor's domain service depends on. It's phrased in the
DOCTOR's language ("supervised patient", "alerts for a patient under my care"),
NOT the patient domain's. The concrete implementation (infrastructure/patient_acl.py)
is the Anti-Corruption Layer: it calls the patient domain's OHS over MCP and
translates the results into the doctor's own models.

Because the service depends only on this interface, the doctor domain can be
tested with a fake data source (no MCP, no patient server) — and the real API can
be swapped in later by changing only the adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.servers.doctor.domain.models import (
    SupervisedPatient, SupervisedAlert, PatientMatch, ReportDispatch)


class PatientDataSource(ABC):
    """The doctor domain's port onto patient data. Implemented by the ACL adapter
    (which reaches the patient domain's OHS). Every method is scoped to a doctor —
    the underlying authorization (care relationship) is enforced by the provider."""

    @abstractmethod
    def resolve_patient(self, call_id: str, doctor_id: str, full_name: str) -> List[PatientMatch]:
        """Name → 0/1/many patients UNDER THIS DOCTOR'S CARE (for disambiguation)."""
        ...

    @abstractmethod
    def get_supervised_patient(self, call_id: str, doctor_id: str,
                               patient_id: str) -> Optional[SupervisedPatient]:
        """Monitoring status for a patient under this doctor's care, or None if the
        provider denies access / no record."""
        ...

    @abstractmethod
    def get_alerts(self, call_id: str, doctor_id: str, patient_id: str) -> List[SupervisedAlert]:
        """Clinical alerts for a patient under this doctor's care."""
        ...

    @abstractmethod
    def send_report_email(self, call_id: str, doctor_id: str,
                          patient_id: str, doctor_email: str) -> ReportDispatch:
        """Email the patient's report to the REGISTERED address (safe action).
        Off-protocol destinations must be refused → connect_human."""
        ...
