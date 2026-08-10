"""
Doctor infrastructure — wiring (dependency injection for this domain).

Builds: MCP caller → ACL adapter (PatientDataSource) → DoctorService. The service
depends only on the PatientDataSource port, so swapping the ACL's data source
(mock patient MCP now, real patient API later) changes nothing here or above.
"""

from __future__ import annotations

from typing import Optional

from src.servers.doctor.domain.service.services import DefaultDoctorService
from src.servers.doctor.infrastructure.patient_acl import PatientMCPAdapter
from src.servers.doctor.infrastructure.mcp_caller import MCPCaller
from src.servers.doctor.infrastructure.doctor_directory import DoctorDirectory


def build_doctor_service(caller: Optional[object] = None) -> DefaultDoctorService:
    caller = caller or MCPCaller()
    data_source = PatientMCPAdapter(caller)      # the ACL
    doctor_directory = DoctorDirectory()
    return DefaultDoctorService(data_source, doctor_directory)