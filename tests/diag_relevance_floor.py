"""
diag_relevance_floor.py — show the RAW cosine(request, narration) for the real
turns, so we calibrate _REL_FLOOR from data instead of guessing.

The thank-you→order-dump case scored req_rel=0.35 (a near-miss). Is that because
its raw cosine is genuinely close to the clean turns (cosine can't separate them),
or because the floor is mis-set? This prints raw cosines for rot vs clean so the
gap is visible.

Run on the Mac:  PYTHONPATH=. python3 tests/diag_relevance_floor.py
"""
from __future__ import annotations
import sys
sys.path.insert(0, ".")
from src.client.faithfulness import _cos  # noqa: E402

CASES = [
    # (label, request, narration, expect)
    ("ROT thank-you→orders",
     "Thank you for taking care of it – I appreciate your help.",
     "You're very welcome — I'm glad I could help, and your recent HeartBug orders are the "
     "HeartBug Monitor, delivered on 18 July, and Replacement Leads, currently in transit "
     "with Australia Post and due on 4 August.", "rot"),
    ("clean airplane mode",
     "does it still work in airplane mode? My phone is going out of service for a flight.",
     "Yes — HeartBug can keep recording without your phone, and it will sync back to the app "
     "over Bluetooth and upload once your phone has internet again.", "clean"),
    ("clean list orders",
     "I was wondering about my order, could you tell me when it will arrive so I can plan?",
     "Your HeartBug Monitor was delivered on July 8, your Electrode Stickers shipped and are "
     "due by July 30, and your Replacement Device is still processing for August 2.", "clean"),
    ("clean no carrier (honest)",
     "who's delivering it on August 2? Will someone come to my home or do I pick it up?",
     "Your Replacement Device is still processing for August 2, and I don't have any carrier, "
     "tracking, or delivery method yet, so I can't say whether it will come to your home.", "clean"),
    ("clean leads confirm",
     "can you confirm the new leads are being sent to my address?",
     "Yes — the Replacement Leads are already on their way with Australia Post to your HeartBug "
     "address, and they're due to arrive on 4 August.", "clean"),
]

def run():
    print(f"{'expect':>6} {'cosine':>7}  label")
    print("-" * 60)
    rot, clean = [], []
    for label, req, narr, expect in CASES:
        c = _cos(req, narr)
        (rot if expect == "rot" else clean).append(c)
        print(f"{expect:>6} {c:>7.3f}  {label}")
    print("-" * 60)
    print(f"ROT cosines:   {[round(x,3) for x in rot]}  (want LOW — off-topic)")
    print(f"clean cosines: {[round(x,3) for x in clean]}  (want HIGH — on-topic)")
    if rot and clean:
        print(f"\nrot max = {max(rot):.3f}, clean min = {min(clean):.3f}")
        if max(rot) < min(clean):
            print(f"→ SEPARABLE. Set _REL_FLOOR between {max(rot):.2f} and {min(clean):.2f}.")
        else:
            print("→ OVERLAP: cosine alone can't separate these; relevance needs help "
                  "(e.g. detect pure-social turns explicitly).")

if __name__ == "__main__":
    run()
