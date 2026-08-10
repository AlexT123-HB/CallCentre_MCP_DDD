"""
Doctor infrastructure — the ANTI-CORRUPTION LAYER (ACL).

This adapter implements the doctor domain's PatientDataSource port by calling the
PATIENT domain's clinician-facing OHS tools over MCP, then TRANSLATING the returned
DTOs into the doctor domain's own models (SupervisedPatient, SupervisedAlert, ...).

Two responsibilities, both anti-corruption:
  1. Shape isolation — the patient domain's response shape never enters the doctor
     domain; only the doctor's own models cross this boundary.
  2. Privilege — the call carries the SERVICE token (the app-admin role), because a
     domain reaching another domain on a caller's behalf is a privileged service-to-
     service call, distinct from the doctor's own caller identity.

The patient domain enforces the CARE RELATIONSHIP (A1); this adapter just consumes
the result. Swapping the mock/patient-MCP for the real patient API later means
changing ONLY this file — the doctor domain's service/models/tools are untouched.
"""

from __future__ import annotations

import os
import logging
from typing import List, Optional

from src.servers.doctor.domain.models import (
    SupervisedPatient, SupervisedAlert, PatientMatch, ReportDispatch)
from src.servers.doctor.domain.repository.interfaces import PatientDataSource

_log = logging.getLogger("heartbug")

class PatientMCPAdapter(PatientDataSource):
    """ACL over the patient domain's OHS. `caller` is an object exposing a
    synchronous `call(domain, tool, **args) -> dict` (injected, so the doctor
    domain doesn't hard-depend on a transport)."""

    def __init__(self, caller, service_token: Optional[str] = None) -> None:
        self._caller = caller
        self._token = service_token or os.getenv("HEARTBUG_SERVICE_TOKEN",
                                                 "internal-service-secret")

    # ── name → supervised patients (disambiguation happens in the tool layer) ──
    async def resolve_patient(self, call_id: str, doctor_id: str, full_name: str) -> List[PatientMatch]:
        res = await self._caller.call_async("patient", "resolve_patient_by_name",
                                            call_id=call_id, service_token=self._token,
                                            doctor_id=doctor_id, full_name=full_name) or {}
        _log.info(f"[{call_id}] Resolved patient for doctor {doctor_id}: {res}")
        if not res.get("ok"):
            _log.info(f"[{call_id}] Failed to resolve patient for doctor {doctor_id}: {res.get('reason')}")
            return []
        # TRANSLATE: patient DTO {patient_id,name} → doctor's PatientMatch
        return [PatientMatch(patient_id=m["patient_id"], name=m.get("name") or "")
                for m in res.get("matches", [])]

    async def get_supervised_patient(self, call_id: str, doctor_id: str,
                                     patient_id: str) -> Optional[SupervisedPatient]:
        res = await self._caller.call_async("patient", "get_patient_monitoring",
                                            call_id=call_id, service_token=self._token,
                                            doctor_id=doctor_id, patient_id=patient_id) or {}
        if not res.get("ok") or not res.get("found"):
            return None
        # TRANSLATE: patient monitoring DTO → doctor's SupervisedPatient
        # (device ids, payment/medicare, internal notes in the real payload are
        #  NOT carried across — the doctor doesn't need them)
        return SupervisedPatient(
            patient_id=patient_id, name=res.get("name", ""),
            enrolment_status=res.get("enrolment_status", "unknown"),
            monitoring_end=res.get("monitoring_end", ""),
            report_ready=bool(res.get("report_ready")),
            report_id=res.get("report_id"))

    async def get_alerts(self, call_id: str, doctor_id: str, patient_id: str) -> List[SupervisedAlert]:
        res = await self._caller.call_async("patient", "get_patient_alerts",
                                            call_id=call_id, service_token=self._token,
                                            doctor_id=doctor_id, patient_id=patient_id) or {}
        if not res.get("ok"):
            return []
        # TRANSLATE: patient alert DTOs → doctor's SupervisedAlert (drop internal ids)
        return [SupervisedAlert(severity=a.get("severity", ""), kind=a.get("kind", ""),
                                raised_at=a.get("raised_at", ""), note=a.get("note", ""))
                for a in res.get("alerts", [])]

    async def send_report_email(self, call_id: str, doctor_id: str,
                                patient_id: str, doctor_email: str) -> ReportDispatch:
        # Only the REGISTERED-address send is safe to automate. The patient domain
        # owns the actual dispatch + address; here we translate its outcome.
        sp = await self.get_supervised_patient(call_id, doctor_id, patient_id)
        if sp is None:
            return ReportDispatch(sent=False, reason="no access or no record")
        if not sp.report_ready:
            return ReportDispatch(sent=False, reason="report not ready yet")
        if not doctor_email:
            return ReportDispatch(sent=False, reason="doctor email not found")
        # a real dispatch call would send report_id to doctor_email here
        return ReportDispatch(sent=True,
                            reason=f"sent to the doctor's email ({doctor_email})")