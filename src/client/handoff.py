"""
Host-level HANDOFF service — decides when a call goes to a human.

This is a CROSS-CUTTING host capability (not a domain). It watches the call and
escalates on any of four triggers, routing to the right human queue by role:

  1. VOLUNTARY        — caller asks for a human                → immediate
  2. VERIFICATION STUCK — 3 unproductive verify turns
        (missing slot OR wrong name/dob, counted together)     → escalate
  3. TASK FAILURE     — 3 not-found results at the same task
        (wrong order #, wrong patient # for a report, ...)     → escalate
  4. OUT-OF-SCOPE     — 3 turns the router can't route anywhere → escalate (or end)

It DECIDES; it does not connect. The output is a transfer action that the
telephony layer (Talkdesk / Amazon Connect) acts on.
"""

from __future__ import annotations

from src.shared.security import AccessContext
import logging

THRESHOLD = 3
REPEAT_THRESHOLD = 5   # repeated same-data extraction (cumulative over the call)

# which human queue a role escalates to
ROLE_QUEUE = {
    "patient": "patient_support",
    "doctor": "clinical_desk",
    "technician": "tech_support",
}


class HandoffService:
    """Tracks per-call escalation counters and produces transfer decisions."""
    def __init__(self) -> None:
        self.log = logging.getLogger("heartbug")

    # ── counter bookkeeping (state lives in the session) ──────────────────────
    @staticmethod
    def _counters(session: dict) -> dict:
        return session.setdefault("escalation", {
            "verify_stuck": 0,      # missing-slot OR wrong-value verify turns
            "task_failures": {},    # operation name → consecutive not-found count
            "out_of_scope": 0,
            "repeat_counts": {},    # signature → TOTAL times the SAME request returned the
                                    # SAME result over the call (cumulative, even if
                                    # interspersed) — catches repeated same-data extraction:
                                    # a caller pulling the same record again and again,
                                    # possibly disguised among other questions (abuse pattern).
            "last_results": {},     # signature → last result fingerprint (to detect "same result")
        })

    # ── record outcomes (called by the host after each turn) ──────────────────
    def record_verify_unproductive(self, session: dict) -> None:
        """A verification turn that did NOT verify (missing slot or wrong value)."""
        self._counters(session)["verify_stuck"] += 1

    def reset_verify(self, session: dict) -> None:
        self._counters(session)["verify_stuck"] = 0

    def record_task_outcome(self, session: dict, operation: str, success: bool) -> None:
        """A domain task result. Success resets that task; failure increments it."""
        tf = self._counters(session)["task_failures"]
        tf[operation] = 0 if success else tf.get(operation, 0) + 1

    def record_out_of_scope(self, session: dict) -> None:
        self._counters(session)["out_of_scope"] += 1

    def reset_out_of_scope(self, session: dict) -> None:
        self._counters(session)["out_of_scope"] = 0

    def record_progress(self, session: dict, signature: str,
                        result_fingerprint: str = "") -> None:
        """Repeated same-data extraction / no-progress detection (CUMULATIVE).

        Counts how many times the SAME request returns the SAME result over the whole
        call — even when interspersed with other turns. Catches the pattern where a
        caller keeps pulling the same record again and again (possibly disguised among
        other questions): each turn "succeeds", but no new information is gained. This
        is a possible abuse vector under a verified session.

        `signature` = what the turn was about (domain:tool:key-arg).
        `result_fingerprint` = a cheap hash/summary of the result, so "same answer
        again" is distinguished from "a new answer" (a new result resets the count)."""
        c = self._counters(session)
        if not signature:
            return
        prev = c["last_results"].get(signature)
        if prev is not None and prev == result_fingerprint:
            c["repeat_counts"][signature] = c["repeat_counts"].get(signature, 1) + 1
        else:
            c["repeat_counts"][signature] = 1   # first time, or a NEW result → reset
        c["last_results"][signature] = result_fingerprint

    def reset_after_handoff(self, session: dict) -> None:
        """Once an escalation has been OFFERED/handled, clear the counters that triggered it."""
        c = self._counters(session)
        c["verify_stuck"] = 0
        c["out_of_scope"] = 0
        c["task_failures"] = {}
        c["repeat_counts"] = {}
        c["last_results"] = {}

    # ── the escalation decision ───────────────────────────────────────────────
    def check(self, session: dict, voluntary: bool = False) -> dict | None:
        """
        Return a transfer action dict if the call should escalate, else None.
        Determines the REASON, then builds one transfer action (no per-reason
        branching). `voluntary` = the router detected a request for a human.
        """
        c = self._counters(session)

        reason = None
        if voluntary:
            reason = "voluntary"
        elif c["verify_stuck"] >= THRESHOLD:
            reason = "verification_stuck"
        elif any(n >= THRESHOLD for n in c["task_failures"].values()):
            reason = "task_failure"
        elif c["out_of_scope"] >= THRESHOLD:
            reason = "out_of_scope"
        elif any(n >= REPEAT_THRESHOLD for n in c["repeat_counts"].values()):
            reason = "repeated_extraction"

        if reason is None:
            return None
        self.log.info(f"[{session['call_id']}] HANDOFF escalation_category={reason} role={session['ctx'].role}")
        return self._transfer(session["ctx"], reason)

    # ── action builder (decision only — telephony layer connects) ─────────────
    @staticmethod
    def _transfer(ctx: AccessContext, reason: str) -> dict:
        queue = ROLE_QUEUE.get(ctx.role, "patient_support")
        return {"action": "transfer", "queue": queue, "escalation_category": reason}
