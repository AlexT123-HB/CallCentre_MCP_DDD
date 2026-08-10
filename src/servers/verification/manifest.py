"""Verification domain — self-description for the host."""

import os

from src.shared.manifest import DomainManifest

MANIFEST = DomainManifest(
    name="verification",
    url=os.getenv("VERIFICATION_URL", "http://localhost:3001/mcp"),
    description="verifying the caller's identity by name and date of birth",
    requires_verification=False,        # reachable pre-verification (it IS verification)
    primary_tool="verify_identity",
    tools=["verify_identity"],          # lookup_by_phone runs at webhook, not via selection
    special_flow="verification",        # only special bit: grant() + prewarm on success
    tool_events={"verify_identity": "verified"},
    # required args per tool + the slot-prompt event to use when each is missing
    # only NAME is universally required — the second factor depends on the role
    # (patient: dob, doctor: clinic), so the server tells us which via `expect`.
    required_args={"verify_identity": {"name": "ask_name"}},
    events={
        "greeting_patient": "welcome them to HeartBug and ask for their full name and date of birth",
        "greeting_doctor":  "welcome them to HeartBug and ask for their full name and which clinic they're calling from — do NOT name any clinic ",
        "ask_name":      "ask for the full name on the account",
        "ask_dob":       "ask for the full date of birth including the year",
        "verified":      "confirm they're verified (use caller_name if present) and offer to help, mention NOTHING about orders/devices/meds",
        "ask_clinic":    "ask which HeartBug clinic is on their record — for a patient that's the clinic they're registered with, "
                            "for a doctor the clinic they work at. Do NOT name any clinic yourself, they must tell you",
        "verify_failed": "could not verify, ask them to repeat their details",
        "verify_escalate": "explain you can't verify them over the phone from this number and you'll connect them to the team",
    },
)

