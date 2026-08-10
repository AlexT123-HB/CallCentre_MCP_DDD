"""
Shared common database (mock) — ONE store, all domains read from it.

Per Sri's directive: a single shared database (not siloed per-domain) to avoid
data-sync issues. Domains relate by ID reference (an order holds patient_id; a
care relationship links doctor_id <-> patient_id) — never by embedding each
other's records. Cross-domain views are assembled by the orchestrator.

Tables:
  PATIENTS            patient_id  -> record
  ORDERS              order_id    -> record (references patient_id)
  DOCTORS             doctor_id   -> record (doctors are callers too)
  CARE_RELATIONSHIPS  (doctor_id, patient_id) pairs — doctor delegated access

Order dates are relative to today so the 28-day monitoring window is meaningful.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _days_from_now(n: int) -> str:
    return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")


# -- PATIENTS ------------------------------------------------------------------
PATIENTS = {
    "P001": {"patient_id": "P001", "name": "John Smith",     "dob": "1985-03-15", "phone": "+61412345678", "clinic_id": "C01", "address": "12 Wattle St, Chatswood NSW 2067"},
    "P002": {"patient_id": "P002", "name": "Jane Doe",       "dob": "1990-07-22", "phone": "+61498765432", "clinic_id": "C01", "address": "8 Ocean Ave, Bondi NSW 2026"},
    "P003": {"patient_id": "P003", "name": "Alex Thompson",  "dob": "1990-07-22", "phone": "+61481103973", "clinic_id": "C02", "address": "45 Collins St, Melbourne VIC 3000"},
    "P004": {"patient_id": "P004", "name": "Maria Garcia",   "dob": "1978-11-30", "phone": "+61423456789", "clinic_id": "C02", "address": "3 Fitzroy Rd, Carlton VIC 3053"},
    "P005": {"patient_id": "P005", "name": "David Chen",     "dob": "1965-05-08", "phone": "+61434567890", "clinic_id": "C01", "address": "27 Harbour Ln, Manly NSW 2095"},
    "P006": {"patient_id": "P006", "name": "Sarah Williams", "dob": "1992-02-17", "phone": "+61445678901", "clinic_id": "C03", "address": "5 River Tce, South Brisbane QLD 4101"},
    "P007": {"patient_id": "P007", "name": "Ahmed Hassan",   "dob": "1988-09-03", "phone": "+61456789012", "clinic_id": "C04", "address": "19 Kings Park Rd, West Perth WA 6005"},
}

# phone -> patient_id (the identity index used by verification)
PHONE_INDEX = {p["phone"]: pid for pid, p in PATIENTS.items()}


# -- ORDERS --------------------------------------------------------------------
# Each order references a patient_id. created_at drives the 28-day window cache.
ORDERS = {
    "O1001": {"order_id": "O1001", "patient_id": "P001", "product": "HeartBug Monitor",
              "status": "delivered",  "created_at": _days_ago(20),
              "carrier": "Australia Post", "tracking": "AP1001AU", "eta": _days_ago(15)},
    "O1002": {"order_id": "O1002", "patient_id": "P001", "product": "Replacement Leads",
              "status": "in_transit", "created_at": _days_ago(3),
              "carrier": "Australia Post", "tracking": "AP1002AU", "eta": _days_from_now(2)},
    "O1003": {"order_id": "O1003", "patient_id": "P002", "product": "HeartBug Monitor",
              "status": "delivered",  "created_at": _days_ago(25),
              "carrier": "StarTrack",      "tracking": "ST1003AU", "eta": _days_ago(21)},
    "O1004": {"order_id": "O1004", "patient_id": "P002", "product": "Electrode Stickers",
              "status": "shipped",    "created_at": _days_ago(5),
              "carrier": "Australia Post", "tracking": "AP1004AU", "eta": _days_from_now(1)},
    "O1005": {"order_id": "O1005", "patient_id": "P002", "product": "Replacement Device",
              "status": "processing", "created_at": _days_ago(1),
              "carrier": None,             "tracking": None,       "eta": _days_from_now(4)},
    "O1006": {"order_id": "O1006", "patient_id": "P003", "product": "HeartBug Monitor",
              "status": "in_transit", "created_at": _days_ago(2),
              "carrier": "StarTrack",      "tracking": "ST1006AU", "eta": _days_from_now(3)},
    "O1007": {"order_id": "O1007", "patient_id": "P004", "product": "HeartBug Monitor",
              "status": "delivered",  "created_at": _days_ago(18),
              "carrier": "Australia Post", "tracking": "AP1007AU", "eta": _days_ago(14)},
    "O1008": {"order_id": "O1008", "patient_id": "P004", "product": "Replacement Leads",
              "status": "shipped",    "created_at": _days_ago(4),
              "carrier": "StarTrack",      "tracking": "ST1008AU", "eta": _days_from_now(2)},
    "O1009": {"order_id": "O1009", "patient_id": "P005", "product": "HeartBug Monitor",
              "status": "delivered",  "created_at": _days_ago(26),
              "carrier": "Australia Post", "tracking": "AP1009AU", "eta": _days_ago(22)},
    "O1010": {"order_id": "O1010", "patient_id": "P006", "product": "Electrode Stickers",
              "status": "in_transit", "created_at": _days_ago(6),
              "carrier": "Australia Post", "tracking": "AP1010AU", "eta": _days_from_now(1)},
    "O1011": {"order_id": "O1011", "patient_id": "P007", "product": "HeartBug Monitor",
              "status": "processing", "created_at": _days_ago(1),
              "carrier": None,             "tracking": None,       "eta": _days_from_now(5)},
    "O1012": {"order_id": "O1012", "patient_id": "P007", "product": "Replacement Device",
              "status": "shipped",    "created_at": _days_ago(7),
              "carrier": "StarTrack",      "tracking": "ST1012AU", "eta": _days_from_now(1)},
}

# patient_id -> [order_id, ...]
ORDERS_BY_PATIENT: dict = {}
for _oid, _o in ORDERS.items():
    ORDERS_BY_PATIENT.setdefault(_o["patient_id"], []).append(_oid)


# -- DOCTORS -------------------------------------------------------------------
# Doctors are CALLERS too (not patients). Identity by doctor_id.
DOCTORS = {
    "D001": {"doctor_id": "D001", "name": "Dr Emily Nguyen",   "dob": "1975-06-12",
             "phone": "+61467890123", "specialty": "Cardiology",
             "email": "emily.nguyen@heartbug.com.au", "clinic_id": "C01"},
    "D002": {"doctor_id": "D002", "name": "Dr Robert Brown",   "dob": "1968-04-25",
             "phone": "+61478901234", "specialty": "Cardiology",
             "email": "robert.brown@heartbug.com.au", "clinic_id": "C02"},
    "D003": {"doctor_id": "D003", "name": "Dr Priya Patel",    "dob": "1982-12-01",
             "phone": "+61489012345", "specialty": "Electrophysiology",
             "email": "priya.patel@heartbug.com.au", "clinic_id": "C03"},
    "D004": {"doctor_id": "D004", "name": "Dr Michael O'Brien","dob": "1971-08-19",
             "phone": "+61490123456", "specialty": "General Practice",
             "email": "michael.obrien@heartbug.com.au", "clinic_id": "C04"},
    "D005": {"doctor_id": "D005", "name": "Dr Lisa Anderson",  "dob": "1979-03-07",
             "phone": "+61401234567", "specialty": "Cardiology",
             "email": "lisa.anderson@heartbug.com.au", "clinic_id": "C01"},
}

# Each patient/doctor belongs to exactly ONE clinic (1→1). clinic_id is a general
# identity attribute carried on the AccessContext for any verified caller.
CLINICS = {
    "C01": {"clinic_id": "C01", "name": "Sydney Heart Centre",        "address": "Level 4, 100 George St, Sydney NSW 2000"},
    "C02": {"clinic_id": "C02", "name": "Melbourne Cardiac Clinic",    "address": "220 Spencer St, Melbourne VIC 3000"},
    "C03": {"clinic_id": "C03", "name": "Brisbane Rhythm Institute",   "address": "70 Grey St, South Brisbane QLD 4101"},
    "C04": {"clinic_id": "C04", "name": "Perth Cardiology Associates", "address": "15 Hay St, Subiaco WA 6008"},
}


def clinic_name(clinic_id: str) -> str:
    return (CLINICS.get(clinic_id) or {}).get("name", clinic_id or "")


def clinic_address(clinic_id: str) -> str:
    """The clinic's registered shipping address (where clinic restocks are sent)."""
    return (CLINICS.get(clinic_id) or {}).get("address", "")


def patient_address(patient_id: str) -> str:
    """The patient's own registered shipping address (where their orders are sent)."""
    return (PATIENTS.get(patient_id) or {}).get("address", "")

# doctor phone -> doctor_id (identity index for doctor callers)
DOCTOR_PHONE_INDEX = {d["phone"]: did for did, d in DOCTORS.items()}


# -- CARE RELATIONSHIPS --------------------------------------------------------
# Which patients each doctor may access (delegated-access authorization).
CARE_RELATIONSHIPS = [
    ("D001", "P001"), ("D001", "P002"), ("D001", "P005"),
    ("D002", "P003"), ("D002", "P004"),
    ("D003", "P002"), ("D003", "P006"),
    ("D004", "P007"),
    ("D005", "P001"), ("D005", "P006"),
]

# doctor_id -> {patient_id, ...}
PATIENTS_BY_DOCTOR: dict = {}
for _did, _pid in CARE_RELATIONSHIPS:
    PATIENTS_BY_DOCTOR.setdefault(_did, set()).add(_pid)


def doctor_can_access(doctor_id: str, patient_id: str) -> bool:
    """Does this doctor have a care relationship with this patient?"""
    return patient_id in PATIENTS_BY_DOCTOR.get(doctor_id, set())


# lower-cased full name -> [patient_id, ...] (a name may map to several patients)
PATIENT_NAME_INDEX: dict = {}
for _pid, _p in PATIENTS.items():
    PATIENT_NAME_INDEX.setdefault(_p["name"].strip().lower(), []).append(_pid)


# -- PATIENT MONITORING --------------------------------------------------------
# Enrolment + monitoring-window status per patient, plus whether the clinical
# report is ready. monitoring_end is relative to today so "still monitoring" is
# meaningful. report_ready=False while the window is open.
MONITORING = {
    "P001": {"patient_id": "P001", "enrolment_status": "active",
             "monitoring_end": _days_from_now(8),  "report_ready": False,
             "report_id": "R001"},
    "P002": {"patient_id": "P002", "enrolment_status": "active",
             "monitoring_end": _days_from_now(3),  "report_ready": False,
             "report_id": "R002"},
    "P003": {"patient_id": "P003", "enrolment_status": "completed",
             "monitoring_end": _days_ago(2),       "report_ready": True,
             "report_id": "R003"},
    "P004": {"patient_id": "P004", "enrolment_status": "completed",
             "monitoring_end": _days_ago(10),      "report_ready": True,
             "report_id": "R004"},
    "P005": {"patient_id": "P005", "enrolment_status": "active",
             "monitoring_end": _days_from_now(14), "report_ready": False,
             "report_id": "R005"},
    "P006": {"patient_id": "P006", "enrolment_status": "completed",
             "monitoring_end": _days_ago(1),       "report_ready": False,   # in progress
             "report_id": "R006"},
    "P007": {"patient_id": "P007", "enrolment_status": "active",
             "monitoring_end": _days_from_now(20), "report_ready": False,
             "report_id": "R007"},
}


# -- PATIENT ALERTS ------------------------------------------------------------
# Clinical alerts raised during monitoring. A patient with no key here has none.
ALERTS = {
    "P002": [{"alert_id": "A001", "patient_id": "P002", "severity": "high",
              "kind": "atrial_fibrillation", "raised_at": _days_ago(1),
              "note": "Irregular rhythm episode detected overnight"}],
    "P004": [{"alert_id": "A002", "patient_id": "P004", "severity": "medium",
              "kind": "bradycardia", "raised_at": _days_ago(6),
              "note": "Sustained low heart rate during sleep"},
             {"alert_id": "A003", "patient_id": "P004", "severity": "low",
              "kind": "lead_off", "raised_at": _days_ago(3),
              "note": "Electrode contact briefly lost"}],
    "P006": [{"alert_id": "A004", "patient_id": "P006", "severity": "high",
              "kind": "tachycardia", "raised_at": _days_ago(2),
              "note": "Elevated heart rate sustained over 20 minutes"}],
}


# -- SERVICE-TO-SERVICE (admin) role -------------------------------------------
# The app-admin identity a domain uses when it calls ANOTHER domain on a verified
# caller's behalf (e.g. doctor domain → patient domain). Distinct from a caller.
SERVICE_ROLE = "service"
_SERVICE_TOKEN = os.getenv("HEARTBUG_SERVICE_TOKEN", "internal-service-secret")


def is_valid_service_token(token: str | None) -> bool:
    """Does this token authorize a privileged service-to-service call? In
    production this is a signed token / mTLS identity; here it's a shared secret
    so the trust boundary is explicit and testable."""
    return bool(token) and token == _SERVICE_TOKEN
