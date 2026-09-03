#!/usr/bin/env python3
"""
Check how narration ACTUALLY works — calls the real Narrator + LLM provider and
prints the spoken reply for a given event + facts + history.

Run INSIDE the container (it needs OPENAI_API_KEY and the src on path):
    docker compose exec host python /app/check_narration.py
Or locally if your env is set up:
    python check_narration.py

Edit the CASES list to test any event. Each case is (label, facts, history).
The narrator reads the event's instruction from the manifests automatically.
"""
import sys
sys.path.insert(0, "/app")          # container path; harmless locally if already on path

from src.client.narration import Narrator
from src.client.providers.factory import get_provider


# ── the cases to check — add/edit freely ──────────────────────────────────────
# facts = what the tool returned (must include "event")
# history = the conversation leading up to it (list of {role, content})
CASES = [
    (
        "callback_need_date  (caller gave a day at escalation → should ASK, not confirm)",
        {"found": True, "recorded": False, "event": "ask_date"},
        [
            {"role": "assistant", "content": "I can connect you with the team now, "
                                             "or schedule a callback for later?"},
            {"role": "user", "content": "call me later, on Thursday"},
        ],
    ),
    (
        "callback_need_time  (has the day, needs the time)",
        {"found": True, "recorded": False, "event": "callback_need_time"},
        [
            {"role": "assistant", "content": "Which day would you like the callback?"},
            {"role": "user", "content": "Thursday"},
        ],
    ),
    (
        "callback_confirm  (both slots set → read back, ask yes/no, NOT booked yet)",
        {"found": True, "needs_confirmation": True, "event": "callback_confirm",
         "scheduled_for": "2026-09-04T09:00", "window_min": 30, "reason": ""},
        [{"role": "user", "content": "Thursday at 9am"}],
    ),
    (
        "callback_recorded  (booked — SHOULD confirm)",
        {"found": True, "recorded": True, "event": "callback_recorded",
         "booking_id": "CB-1234AB", "scheduled_for": "2026-09-04T09:00", "window_min": 30},
        [{"role": "user", "content": "yes, book it"}],
    ),
    (
        "callback_out_of_hours  (should refuse + ask for a valid time)",
        {"found": True, "recorded": False, "event": "callback_out_of_hours",
         "why": "weekend", "open_hour": 9, "close_hour": 17},
        [{"role": "user", "content": "call me saturday at 9am"}],
    ),
]


def main():
    narrator = Narrator(get_provider())
    for label, facts, history in CASES:
        print("\n" + "=" * 74)
        print(label)
        print("-" * 74)
        print("facts  :", facts)
        print("history:", " | ".join(f"{m['role']}:{m['content']}" for m in history))
        caller_said = history[-1]["content"] if history else ""
        try:
            reply = narrator.say(facts, caller_said, history)
            print("REPLY  :", reply)
        except Exception as e:                       # noqa: BLE001
            print("ERROR  :", repr(e))
    print("\n" + "=" * 74)
    print("Check each REPLY against what the event SHOULD do:")
    print("  need_date/need_time → should ASK, never say 'noted/booked'")
    print("  confirm             → should read back + ask yes/no, NOT say booked")
    print("  recorded            → SHOULD confirm it's booked")
    print("  out_of_hours        → should refuse + ask for a valid time")


if __name__ == "__main__":
    main()
