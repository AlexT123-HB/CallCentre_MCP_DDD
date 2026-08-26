"""
Doctor domain — self-description for the host.

Doctors are verified callers who act on PATIENTS UNDER THEIR CARE (not on
themselves). The domain reads patient data through an Anti-Corruption Layer over
the patient domain's OHS; the care-relationship authorization is enforced by the
patient domain. Doctors also remain a caller-identity source (phone → doctor).
"""

import os

from src.shared.manifest import DomainManifest

MANIFEST = DomainManifest(
    name="doctor",
    url=os.getenv("DOCTOR_URL", "http://localhost:3005/mcp"),
    description=("a verified DOCTOR's clinical view of THEIR OWN patients — a "
                 "patient's monitoring status, clinical alerts, and sending a "
                 "patient's report. NOT the doctor's own record, NOT ordering."),
    requires_verification=True,
    primary_tool="check_patient_status",
    tools=["check_patient_status", "check_patient_alerts", "send_patient_report"],
    tool_events={
        "check_patient_status": "doctor_patient_status",
        "check_patient_alerts": "doctor_patient_alerts",
        "send_patient_report":  "doctor_report_sent",
    },
    required_args={
        "check_patient_status": {"patient_name": "ask_patient_name"},
        "check_patient_alerts": {"patient_name": "ask_patient_name"},
        "send_patient_report":  {"patient_name": "ask_patient_name"},
    },
    special_flow=None,
    identity_source={
        "table": "DOCTORS",
        "id_field": "doctor_id",
        "phone_index": "DOCTOR_PHONE_INDEX",
        "role": "doctor",
    },
    events={
        "doctor_patient_status": "tell the doctor how their patient is going: enrolment "
                                 "status, how long is left in monitoring (from monitoring_end), "
                                 "and whether the report is ready — briefly and clearly",
        "doctor_patient_alerts": "tell the doctor about their patient's alerts from the facts "
                                 "(severity + what each is); if alert_count is 0, say no alerts "
                                 "have been raised",
        "doctor_report_sent":    "confirm the patient's report has been emailed to the doctor's "
                                 "registered address",
        "doctor_report_unavailable": "explain the report can't be sent yet (from the reason) — "
                                     "e.g. the monitoring report isn't ready",
        "doctor_report_escalate": "tell the doctor you'll pass this to a team member to handle",
        "doctor_no_patient":     "tell the doctor you couldn't find a patient by that name under "
                                 "their care, and ask them to confirm the patient's full name",
        "doctor_disambiguate":   "tell the doctor more than one of their patients matches that "
                                 "name (list the candidates from the facts) and ask which one",
        "ask_patient_name":      "ask the doctor which patient (the patient's full name) they "
                                 "want this for",
        "doctor_role_required":  "explain that only a verified doctor can do this, Offer to "
                                 "help with their own account.",
    },
)
