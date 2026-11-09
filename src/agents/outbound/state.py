"""Per-call state shared by the phase agents.

The worker starts WITHOUT the reason for the call or the organisation name; it
receives them from the host only when verification passes (mark_verified).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.shared.outbound_contract import Briefing, DispatchMetadata, Outcome


class NotVerified(PermissionError):
    pass


@dataclass
class CallState:
    meta: DispatchMetadata
    _briefing: Briefing
    verified: bool = False
    check_attempts: int = 0
    heard_callee: bool = False
    outcome: Outcome | None = None
    detail: str | None = None
    sip_status_code: int | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    # LATER (handoff to a human): staff_joined: asyncio.Event = field(default_factory=asyncio.Event)
    _reason: str | None = None
    #_organisation: str | None = None

    # ── from the briefing (safe before verification) ──
    @property
    def first_name(self) -> str:
        return self._briefing.patient_first_name

    @property
    def intro(self) -> str:
        return self._briefing.intro

    @property
    def slots(self) -> dict[str, str]:
        return self._briefing.check.slots

    @property
    def checks_exhausted(self) -> bool:
        return self.check_attempts >= self._briefing.max_check_attempts

    @property
    def voicemail_message(self) -> str | None:
        return self._briefing.voicemail_message

    # ── from the host's verify answer ──
    def mark_verified(self, reason: str) -> None:
        self.verified = True
        self._reason = reason

    @property
    def reason(self) -> str:
        if not self.verified:
            raise NotVerified("reason is not available before verification")
        return self._reason or ""

    def finish(self, outcome: Outcome, detail: str | None = None) -> None:
        """First outcome wins; later ones (e.g. the hang-up that follows) are ignored."""
        if self.outcome is None:
            self.outcome, self.detail = outcome, detail
        self.done.set()
