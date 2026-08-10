"""Patient domain — self-description for the host."""

import os

from src.shared.manifest import DomainManifest

MANIFEST = DomainManifest(
    name="patient",
    url=os.getenv("PATIENT_URL", "http://localhost:3002/mcp"),
    description="the caller's own patient record (their name and date of birth on file)",
    requires_verification=True,         # verified-only
    #primary_event="my_info",
    primary_tool="get_my_info",
    tools=["get_my_info", "prewarm"],
    special_flow=None,                  # generic domain flow
    events={
        "my_info":   "tell them their own name and date of birth from the facts",
        "patient_not_found": "could not find the record, ask them to double-check the detail",
    },
    identity_source={
        "table": "PATIENTS",
        "phone_index": "PHONE_INDEX",
        "id_field": "patient_id",
        "role": "patient"
    },
)
