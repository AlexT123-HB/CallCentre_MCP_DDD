"""Patient service interface — patient DATA only (verification is its own domain)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.servers.patient.domain.models import Patient, MonitoringStatus, PatientAlert
from src.shared.security import AccessContext


class PatientService(ABC):
    @abstractmethod
    def find_self(self, ctx: AccessContext) -> Optional[Patient]:
        """The verified caller's OWN record only."""
        ...

    # ── clinician-facing (OHS consumed by the doctor domain, service-to-service) ──
    # These enforce the CARE RELATIONSHIP: a clinician may only read a patient they
    # have a care relationship with. Authorization lives HERE (Information Expert:
    # the patient domain owns who-may-see-patient-data), not in the doctor domain.

    @abstractmethod
    def find_for_clinician(self, ctx: AccessContext, doctor_id: str,
                           patient_id: str) -> Optional[Patient]:
        """A patient's basic record, only if `doctor_id` has a care relationship
        with them. Requires a service-identity context."""
        ...

    @abstractmethod
    def resolve_by_name_for_clinician(self, ctx: AccessContext, doctor_id: str,
                                      full_name: str) -> list:
        """Patient_ids under this doctor's care matching a full name (0, 1, or many
        → the doctor domain disambiguates). Scoped to the care relationship."""
        ...

    @abstractmethod
    def monitoring_for_clinician(self, ctx: AccessContext, doctor_id: str,
                                 patient_id: str) -> Optional[MonitoringStatus]:
        """A patient's monitoring status, gated on the care relationship."""
        ...

    @abstractmethod
    def alerts_for_clinician(self, ctx: AccessContext, doctor_id: str,
                             patient_id: str) -> list:
        """A patient's clinical alerts, gated on the care relationship."""
        ...