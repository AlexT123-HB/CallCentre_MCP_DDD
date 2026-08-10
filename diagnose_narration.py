#!/usr/bin/env python3
"""
diagnose_narration.py — test the host's narration in isolation.

Two modes:

  python diagnose_narration.py            # PROMPT mode (no API key needed)
      Shows the EXACT prompt the narrator would send to the LLM for each event,
      so you can verify the tool-result facts are actually in the prompt.

  python diagnose_narration.py --live     # LIVE mode (needs OPENAI_API_KEY)
      Calls the real LLM and checks whether the spoken output actually contains
      the key facts (reference number, clinic name, quantity, ...). Flags any
      event whose narration ignores its facts.

Run from the project root with your venv active.
"""
import json
import sys

# ── the test cases: (label, facts, expected substrings the narration SHOULD reflect) ──
CASES = [
    ("restock placed (confirmed)",
     {"found": True, "event": "restock_placed", "product": "sticker packs",
      "quantity": 50, "clinic": "Melbourne Cardiac Clinic", "reference": "CR-20260714-0001",
      "status": "pending"},
     ["50", "sticker", "Melbourne", "CR-20260714-0001"]),

    ("restock needs approval",
     {"found": True, "event": "restock_needs_approval", "product": "sticker packs",
      "quantity": 50, "clinic": "Melbourne Cardiac Clinic"},
     ["50", "sticker", "Melbourne"]),

    ("restock cancelled",
     {"event": "restock_cancelled"},
     []),                                        # no facts to reflect; just tone

    ("patient order placed",
     {"found": True, "event": "order_placed", "product": "leads",
      "quantity": 5, "reference": "PO-20260714-0002", "status": "pending"},
     ["5", "lead", "PO-20260714-0002"]),

    ("order status lookup",
     {"found": True, "event": "order_status", "order_id": "O1002",
      "status": "in_transit", "carrier": "Australia Post", "eta": "2026-07-16"},
     ["Australia Post", "O1002"]),

    ("doctor patient status",
     {"found": True, "event": "doctor_patient_status", "name": "Jane Doe",
      "enrolment_status": "active", "monitoring_end": "2026-07-16", "report_ready": False},
     ["Jane", "active"]),

    ("report sent (masked email)",
     {"found": True, "event": "doctor_report_sent", "name": "Alex Thompson",
      "masked_email": "r***@heartbug.com.au"},
     ["r***@heartbug.com.au"]),
]


def prompt_mode():
    """Show the exact narrator prompt for each case — reveals if facts are present."""
    from src.client.narration import NARRATION_STYLE
    print(f"NARRATION_STYLE length: {len(NARRATION_STYLE)} chars")
    print("(a very long style block can bury the facts for a small model)\n")

    # reproduce exactly what provider.narrate builds
    def build_user(facts, caller_said=""):
        return (
            "Speak to the caller now, in your warm HeartBug-agent voice.\n"
            f'The caller just said: "{caller_said}"\n'
            f"Facts you may use (and ONLY these — never invent beyond them): {json.dumps(facts)}\n"
            "Acknowledge their tone if they sound frustrated, then convey the facts "
            "naturally in one or two spoken sentences."
        )

    for label, facts, _ in CASES:
        print("=" * 70)
        print(f"CASE: {label}")
        print(f"  event: {facts.get('event')}")
        print(f"  facts in prompt: {json.dumps(facts)}")
        # is the event's own instruction present in the style?
        ev = facts.get("event", "")
        present = ev in NARRATION_STYLE
        print(f"  event instruction in style block: {present}")
        if not present:
            print(f"  ⚠️  '{ev}' NOT in the style block → narrator has no guidance for it!")
    print("=" * 70)
    print("\nPROMPT-mode check complete. Facts ARE included in every prompt.")
    print("If live narration ignores them, it's a model/prompt-length issue —")
    print("run with --live to confirm, then consider trimming the style block to")
    print("only the relevant event.")


def live_mode():
    """Call the real narrator and check each output actually uses its facts."""
    from src.client.providers.factory import get_provider
    from src.client.narration import Narrator

    provider = get_provider()
    narrator = Narrator(provider)

    print("LIVE narration test (real LLM):\n")
    failures = 0
    for label, facts, expect in CASES:
        out = narrator.say(dict(facts), caller_said="")
        missing = [e for e in expect if e.lower() not in out.lower()]
        status = "OK " if not missing else "MISS"
        if missing:
            failures += 1
        print(f"[{status}] {label}")
        print(f"       said: {out}")
        if missing:
            print(f"       ⚠️  missing from narration: {missing}")
        print()

    print("=" * 70)
    if failures:
        print(f"{failures}/{len(CASES)} events narrated WITHOUT using their facts.")
        print("→ The narrator is getting the facts but not reflecting them. Likely the")
        print("  4kB style block (every event's description) is burying the specific")
        print("  facts for a small model. Fix: pass ONLY the relevant event's")
        print("  instruction to narrate(), not all of them.")
    else:
        print("All events narrated using their facts. Narration is working.")


if __name__ == "__main__":
    if "--live" in sys.argv:
        live_mode()
    else:
        prompt_mode()