"""
Doctor domain models — the doctor context's OWN view of the world.

These are deliberately DIFFERENT from the patient domain's models. The doctor
sees a "supervised patient" (someone under their care), not a patient's own
self-record. The ACL (infrastructure/patient_acl.py) translates the patient
domain's data into THESE models, so the patient domain's shape never leaks in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SupervisedPatient:
    """A patient under the calling doctor's care — the doctor's-eye view. Only the
    fields a clinician needs on a call; PII the doctor doesn't need (device ids,
    payment/medicare numbers, internal notes) is stripped at the ACL."""
    patient_id: str
    name: str
    enrolment_status: str          # active / completed / in-review
    monitoring_end: str
    report_ready: bool
    report_id: Optional[str] = None


@dataclass
class SupervisedAlert:
    """A clinical alert on a supervised patient, in the doctor's model."""
    severity: str
    kind: str
    raised_at: str
    note: str


@dataclass
class PatientMatch:
    """A name-resolution candidate (for disambiguation across turns)."""
    patient_id: str
    name: str


@dataclass
class ReportDispatch:
    """Outcome of a request to email a patient's report."""
    sent: bool
    reason: str = ""
    needs_human: bool = False
    masked_email: str = ""
