"""Doctor domain service interface — clinician operations on supervised patients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.servers.doctor.domain.models import (
    SupervisedPatient, SupervisedAlert, PatientMatch, ReportDispatch)
from src.shared.security import AccessContext


class DoctorService(ABC):
    @abstractmethod
    async def resolve_patient(self, ctx: AccessContext, full_name: str) -> List[PatientMatch]:
        ...

    @abstractmethod
    async def patient_status(self, ctx: AccessContext, patient_id: str) -> Optional[SupervisedPatient]:
        ...

    @abstractmethod
    async def patient_alerts(self, ctx: AccessContext, patient_id: str) -> List[SupervisedAlert]:
        ...

    @abstractmethod
    async def send_report(self, ctx: AccessContext, patient_id: str) -> ReportDispatch:
        ...
