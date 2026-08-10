"""Patient domain service — find the verified caller's own record."""

from __future__ import annotations

from typing import Optional

from src.servers.patient.domain.models import Patient
from src.servers.patient.domain.repository.interfaces import PatientRepository
from src.servers.patient.domain.service.interfaces import PatientService, MonitoringStatus, PatientAlert
from src.shared.security import AccessContext, AccessDenied


class DefaultPatientService(PatientService):
    def __init__(self, repository: PatientRepository) -> None:
        self._repo = repository

    def find_self(self, ctx: AccessContext) -> Optional[Patient]:
        ctx.require_verified()
        return self._repo.get_by_id(ctx.subject_id)   # own id only — cannot widen

    # ── clinician-facing (service-to-service; care-relationship enforced HERE) ──

    def _guard_clinician(self, ctx: AccessContext, doctor_id: str, patient_id: str) -> None:
        """Two gates: (1) the caller must be a trusted SERVICE identity (another
        domain acting on a verified caller's behalf), and (2) the doctor must have
        a CARE RELATIONSHIP with the patient. This is the authorization crux, and
        it lives in the patient domain — the owner of patient data."""
        if not ctx.is_service():
            raise AccessDenied("clinician access requires a service identity")
        if not self._repo.has_care_relationship(doctor_id, patient_id):
            raise AccessDenied("no care relationship with this patient")

    def find_for_clinician(self, ctx: AccessContext, doctor_id: str,
                           patient_id: str) -> Optional[Patient]:
        self._guard_clinician(ctx, doctor_id, patient_id)
        return self._repo.get_by_id(patient_id)

    def resolve_by_name_for_clinician(self, ctx: AccessContext, doctor_id: str,
                                      full_name: str) -> list:
        if not ctx.is_service():
            raise AccessDenied("clinician access requires a service identity")
        # only patients UNDER THIS DOCTOR'S CARE that match the name are returned,
        # so a doctor can't probe for patients outside their care by guessing names
        candidates = self._repo.find_ids_by_name(full_name)
        return [pid for pid in candidates
                if self._repo.has_care_relationship(doctor_id, pid)]

    def monitoring_for_clinician(self, ctx: AccessContext, doctor_id: str,
                                 patient_id: str) -> Optional[MonitoringStatus]:
        self._guard_clinician(ctx, doctor_id, patient_id)
        return self._repo.get_monitoring(patient_id)

    def alerts_for_clinician(self, ctx: AccessContext, doctor_id: str,
                             patient_id: str) -> list:
        self._guard_clinician(ctx, doctor_id, patient_id)
        return self._repo.get_alerts(patient_id)
