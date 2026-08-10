"""Patient repository interface — abstracts WHERE patient data lives (pure)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.servers.patient.domain.models import Patient, MonitoringStatus, PatientAlert


class PatientRepository(ABC):
    @abstractmethod
    def get_by_id(self, patient_id: str) -> Optional[Patient]:
        ...

    @abstractmethod
    def get_by_phone(self, phone: str) -> Optional[Patient]:
        ...

    @abstractmethod
    def has_care_relationship(self, doctor_id: str, patient_id: str) -> bool:
        ...

    @abstractmethod
    def find_ids_by_name(self, full_name: str) -> list:
        ...

    @abstractmethod
    def get_monitoring(self, patient_id: str) -> Optional[MonitoringStatus]:
        ...

    @abstractmethod
    def get_alerts(self, patient_id: str) -> list:
        ...
