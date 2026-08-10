"""Patient domain models — pure business objects, no external dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Patient:
    """A HeartBug patient. Identity is patient_id; phone is optional contact info."""
    patient_id: str
    name: str
    dob: str
    phone: Optional[str] = None

@dataclass
class MonitoringStatus:
    """A patient's enrolment + monitoring-window state, and whether the clinical
    report is ready. Maps to the real 'latestEngagement' when the API is wired."""
    patient_id: str
    enrolment_status: str          # active / completed / in-review
    monitoring_end: str            # ISO date the monitoring window ends
    report_ready: bool
    report_id: Optional[str] = None


@dataclass
class PatientAlert:
    """A clinical alert raised during monitoring."""
    alert_id: str
    patient_id: str
    severity: str                  # low / medium / high
    kind: str
    raised_at: str
    note: str