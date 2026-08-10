"""
Patient infrastructure — wiring (dependency injection for this domain).

Builds the concrete object graph: adaptor → repository → service. Returns a ready
service the application/MCP layer can expose. This is the one place the patient
domain's concrete pieces are assembled.
"""

from __future__ import annotations

from src.servers.patient.domain.service.services import DefaultPatientService
from src.servers.patient.infrastructure.adaptors import PatientDataAdaptor
from src.servers.patient.infrastructure.repositories import PatientRepositoryImpl


def build_patient_service() -> DefaultPatientService:
    adaptor = PatientDataAdaptor()
    repository = PatientRepositoryImpl(adaptor)
    return DefaultPatientService(repository)
