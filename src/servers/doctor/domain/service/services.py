"""
Doctor domain service — clinician operations on patients under their care.

The verified CALLER here is the doctor (ctx.subject_id = doctor_id, role=doctor).
The service works entirely in the doctor's own models and reaches patient data
through the PatientDataSource port (the ACL). It does NOT re-implement the care-
relationship check — that authorization is owned and enforced by the patient
domain (A1). The service's job is clinician-level orchestration: resolve the
patient, fetch status/alerts, request the report email.

Defense in depth: even though the patient domain enforces the care relationship,
this service still requires a verified doctor identity before doing anything.
"""

from __future__ import annotations

from typing import List, Optional

from src.servers.doctor.domain.models import (
    SupervisedPatient, SupervisedAlert, PatientMatch, ReportDispatch)
from src.servers.doctor.domain.repository.interfaces import PatientDataSource
from src.servers.doctor.domain.service.interfaces import DoctorService
from src.servers.doctor.infrastructure.doctor_directory import DoctorDirectory
from src.shared.security import AccessContext, AccessDenied
    


class DefaultDoctorService(DoctorService):
    def __init__(self, data_source: PatientDataSource, doctor_directory: DoctorDirectory) -> None:
        self._patients = data_source
        self._doctors = doctor_directory

    def _doctor(self, ctx: AccessContext) -> str:
        """The verified doctor making the call. Must be a verified doctor caller."""
        ctx.require_verified()
        if ctx.role != "doctor":
            raise AccessDenied("this operation is for verified doctors only")
        return ctx.subject_id

    async def resolve_patient(self, ctx: AccessContext, full_name: str) -> List[PatientMatch]:
        doctor_id = self._doctor(ctx)
        return await self._patients.resolve_patient(ctx.call_id, doctor_id, full_name)

    async def patient_status(self, ctx: AccessContext, patient_id: str) -> Optional[SupervisedPatient]:
        doctor_id = self._doctor(ctx)
        return await self._patients.get_supervised_patient(ctx.call_id, doctor_id, patient_id)

    async def patient_alerts(self, ctx: AccessContext, patient_id: str) -> List[SupervisedAlert]:
        doctor_id = self._doctor(ctx)
        return await self._patients.get_alerts(ctx.call_id, doctor_id, patient_id)

    async def send_report(self, ctx: AccessContext, patient_id: str) -> ReportDispatch:
        doctor_id = self._doctor(ctx)
        doctor_email = self._doctors.email_of(doctor_id)
        return await self._patients.send_report_email(ctx.call_id, doctor_id, patient_id, doctor_email)
