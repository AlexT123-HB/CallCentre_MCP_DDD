"""Outbound call — host side (action: transfer → outbound subagent).

  transfer (host.py) → OutboundCaller.call_now() → subagent dials the given number
  worker → /outbound/.../verify   → verification.verify_identity; on a pass the host
                                    also returns the reason for the call
  worker → /outbound/.../handoff  → staff.offer() (DevStaff: logs the room to join)
  worker → /outbound/.../outcome  → logged; staff hold released unless handed off

The reason for the call stays in the host until verification passes. Each call gets
its own host session ("outbound-<callback>-<token8>") with the patient as
matched_subject_id, so verify_identity behaves exactly as on an inbound call.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from src.agents.outbound import dispatch as subagent
#from src.client.staff import DevStaff, StaffPort
from src.shared.outbound_contract import AttemptResult, Briefing, CheckSpec, Outcome

log = logging.getLogger("heartbug")

CHECK_SLOTS = {
    "name": "their full name",
    "dob": "their date of birth, submitted as YYYY-MM-DD (e.g. 1990-07-30)",
}

class OutboundCaller:
    def __init__(self, client: Any, sessions: Any, start_subagent: Any = None) -> None:
        self._client = client
        self._sessions = sessions
        self._start_subagent = start_subagent or subagent.place_call
        #self._organisation = os.getenv("HEARTBUG_ORG_NAME", "HeartBug")

    # ── trigger: transfer_now ──────────────────────────────────────────────────
    async def call_now(self, session: dict, reason: str, to_number: str | None = None) -> str:
        """Start an outbound call to `to_number` for the verified caller in `session`.
        `reason` stays here and is only released to the worker after verification.
        Returns the callback id."""
        phone = to_number or os.getenv("OUTBOUND_DEMO_TO_NUMBER") or session.get("ani")
        if not phone:
            raise LookupError("no phone number to call")
        patient_id = session["ctx"].subject_id
        me = await self._client.call(session, "patient", "get_my_info") or {}
        name = me.get("name") or ""

        callback_id = f"cb-{session['call_id']}"
        token = secrets.token_urlsafe(16)
        briefing = Briefing(
            callback_id=callback_id,
            to_number=phone,
            patient_first_name=name.split()[0] if name else "there",
            check=CheckSpec(slots=dict(CHECK_SLOTS)),
            intro=os.getenv("HEARTBUG_OUTBOUND_INTRO",
                            "Hello, this is HeartBug, calling back about the request you just made."),
        )
        s = self._sessions.start(self._call_id(callback_id, token), phone)
        s["ctx"].matched_subject_id = patient_id
        s["outbound"] = {"callback_id": callback_id, "attempt_token": token,
                         "patient_id": patient_id, "name": name, "reason": reason,
                         "room": None, "verified": False}
        self._sessions.save(s)
        room = await self._start_subagent(briefing, token)
        s["outbound"]["room"] = room
        self._sessions.save(s)
        log.info(f"[{callback_id}] OUTBOUND call started → room {room} ; reason: {reason}")
        return callback_id

    # ── worker callbacks ───────────────────────────────────────────────────────
    @staticmethod
    def _call_id(callback_id: str, token: str) -> str:
        return f"outbound-{callback_id}-{token[:8]}"

    def _attempt(self, callback_id: str, token: str) -> dict | None:
        """The call's session, only if the full token matches."""
        try:
            s = self._sessions.get(self._call_id(callback_id, token))
        except Exception:                                   # noqa: BLE001
            return None
        o = (s or {}).get("outbound") or {}
        return s if o.get("attempt_token") == token else None

    async def verify(self, callback_id: str, attempt_token: str, answers: dict[str, str]) -> dict:
        """Check name + dob with the verification domain. On a pass, hand back the
        reason for the call; on a fail, nothing else."""
        s = self._attempt(callback_id, attempt_token)
        if not s:
            return {"passed": False}
        res = await self._client.call(s, "verification", "verify_identity", **answers) or {}
        o = s["outbound"]
        passed = bool(res.get("verified")) and res.get("subject_id") == o["patient_id"]
        log.info(f"[{callback_id}] OUTBOUND verify → {passed}")
        if not passed:
            return {"passed": False}
        o["verified"] = True
        self._sessions.save(s)
        return {"passed": True, "reason": o["reason"] or "your request to speak with someone"}

    async def finish_attempt(self, result: AttemptResult) -> None:
        log.info(f"[{result.callback_id}] OUTBOUND outcome: {result.outcome} "
                 f"{result.detail or ''}".rstrip())

    # LATER (handoff to a human):
    # async def start_handoff(self, callback_id: str, attempt_token: str) -> None:
    #     s = self._attempt(callback_id, attempt_token)
    #     if not s or not s["outbound"]["verified"]:
    #         raise PermissionError("unknown, stale or unverified call")
    #     await self._staff.offer(s["outbound"]["reservation_id"], s["outbound"]["room"],
    #                             {"name": s["outbound"]["name"], "reason": s["outbound"]["reason"]})
