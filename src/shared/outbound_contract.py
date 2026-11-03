"""Contract shared by the host, the outbound MCP server and the outbound worker.

Bump SCHEMA_VERSION on any breaking change; every side rejects versions it
doesn't know.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "1"
AGENT_NAME = "heartbug-outbound"  # LiveKit agent name the outbound server dispatches


class ContractError(ValueError):
    """Payload doesn't match this version of the contract."""

def _require(d: dict[str, Any], *keys: str) -> None:
    if d.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(
            f"unsupported schema_version {d.get('schema_version')!r}, expected {SCHEMA_VERSION!r}"
        )
    missing = [k for k in keys if k not in d]
    if missing:
        raise ContractError(f"missing fields: {', '.join(missing)}")

@dataclass(frozen=True)
class DispatchMetadata:
    """Carried in the LiveKit dispatch. No phone number, no PII."""

    callback_id: str
    attempt_token: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_json(cls, raw: str | None) -> DispatchMetadata:
        try:
            d = json.loads(raw or "{}")
        except json.JSONDecodeError as e:
            raise ContractError(f"metadata is not JSON: {e}") from None
        _require(d, "callback_id", "attempt_token")
        return cls(callback_id=d["callback_id"], attempt_token=d["attempt_token"])

    def to_json(self) -> str:
        return json.dumps(asdict(self))

@dataclass(frozen=True)
class CheckSpec:
    """Identity details the agent collects (slot name -> what to ask for).
    The expected values never leave the host."""

    slots: dict[str, str]  # e.g. {"name": "their full name", "dob": "their date of birth"}

    def __post_init__(self) -> None:
        if not self.slots:
            raise ContractError("check needs at least one slot")

@dataclass(frozen=True)
class Briefing:
    """What the worker needs to place the call and run the check. Deliberately has
    NO reason for the call: the host sends those only
    after the callee is verified (see VerifyResult)."""

    callback_id: str
    to_number: str
    patient_first_name: str
    check: CheckSpec
    intro: str  # how to introduce the call before verification
    voicemail_message: str | None = None  # None: hang up without leaving one
    max_check_attempts: int = 2
    # LATER (handoff to a human): handoff_timeout_s: int = 60
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.max_check_attempts < 1:
            raise ContractError("max_check_attempts must be >= 1")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Briefing:
        _require(d, "callback_id", "to_number", "patient_first_name", "check", "intro")
        c = d["check"]
        if not isinstance(c, dict) or not isinstance(c.get("slots"), dict):
            raise ContractError("check.slots must be an object")
        return cls(
            callback_id=d["callback_id"],
            to_number=d["to_number"],
            patient_first_name=d["patient_first_name"],
            check=CheckSpec(slots=dict(c["slots"])),
            intro=d["intro"],
            voicemail_message=d.get("voicemail_message"),
            max_check_attempts=int(d.get("max_check_attempts", 2)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class VerifyResult:
    """The host's answer to /verify. reason is filled ONLY when
    passed — this is the first moment the worker learns why it's calling."""

    passed: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.passed and not self.reason:
            raise ContractError("a passing verification must carry the reason for the call")
        if not self.passed and self.reason is not None:
            raise ContractError("a failed verification must not carry the reason")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VerifyResult:
        if not isinstance(d.get("passed"), bool):
            raise ContractError("verify result needs a boolean 'passed'")
        return cls(passed=d["passed"], reason=d.get("reason"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

class Outcome(StrEnum):
    BRIEFED = "briefed"  # verified, and told why we called (the demo's happy path)
    VOICEMAIL = "voicemail"
    UNVERIFIED = "unverified"
    BAD_TIME = "bad_time"
    WRONG_PERSON = "wrong_person"  # the number on record is probably wrong
    REFUSED = "refused"  # declined to confirm details
    ABANDONED = "abandoned"  # callee hung up mid-call
    FAILED = "failed"  # dial failed or agent error
    # LATER (handoff to a human):
    # HANDED_OFF = "handed_off"
    # HANDOFF_TIMEOUT = "handoff_timeout"

@dataclass(frozen=True)
class AttemptResult:
    """Reported once per attempt. The host decides what happens next."""

    callback_id: str
    attempt_token: str
    outcome: Outcome
    started_at: str
    ended_at: str
    sip_status_code: int | None = None
    detail: str | None = None
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AttemptResult:
        _require(d, "callback_id", "attempt_token", "outcome", "started_at", "ended_at")
        try:
            outcome = Outcome(d["outcome"])
        except ValueError:
            raise ContractError(f"unknown outcome {d['outcome']!r}") from None
        return cls(
            callback_id=d["callback_id"],
            attempt_token=d["attempt_token"],
            outcome=outcome,
            started_at=d["started_at"],
            ended_at=d["ended_at"],
            sip_status_code=d.get("sip_status_code"),
            detail=d.get("detail"),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

