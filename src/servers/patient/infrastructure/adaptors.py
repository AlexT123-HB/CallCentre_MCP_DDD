"""
Patient infrastructure — adaptor.

The adaptor isolates the EXTERNAL data source (here the mock DB; later a real
patient API or SQL DB) and translates raw rows into pure domain models. If the
data source changes, only this file changes.
"""

from __future__ import annotations

from typing import Optional

from src.servers.patient.domain.models import MonitoringStatus, Patient, PatientAlert
from src.shared import database as db


class PatientDataAdaptor:
    """Talks to the patient data source and returns domain models."""

    def fetch_by_id(self, patient_id: str) -> Optional[Patient]:
        return self._to_model(db.PATIENTS.get(patient_id))

    def fetch_by_phone(self, phone: str) -> Optional[Patient]:
        pid = db.PHONE_INDEX.get(phone)
        return self.fetch_by_id(pid) if pid else None
    
    def care_relationship(self, doctor_id: str, patient_id: str) -> bool:
        return db.doctor_can_access(doctor_id, patient_id)

    def ids_by_name(self, full_name: str) -> list:
        return list(db.PATIENT_NAME_INDEX.get(full_name.strip().lower(), []))

    def fetch_monitoring(self, patient_id: str) -> Optional[MonitoringStatus]:
        row = db.MONITORING.get(patient_id)
        if not row:
            return None
        return MonitoringStatus(
            patient_id=row["patient_id"], enrolment_status=row["enrolment_status"],
            monitoring_end=row["monitoring_end"], report_ready=row["report_ready"],
            report_id=row.get("report_id"))

    def fetch_alerts(self, patient_id: str) -> list:
        return [PatientAlert(alert_id=a["alert_id"], patient_id=a["patient_id"],
                             severity=a["severity"], kind=a["kind"],
                             raised_at=a["raised_at"], note=a["note"])
                for a in db.ALERTS.get(patient_id, [])]
    

    @staticmethod
    def _to_model(row: Optional[dict]) -> Optional[Patient]:
        if not row:
            return None
        return Patient(patient_id=row["patient_id"], name=row["name"],
                       dob=row["dob"], phone=row.get("phone"))
