"""
test_relevance_methods.py — compare THREE ways to measure diff(request, narration)
on labeled relevant/irrelevant pairs, so the data (not intuition) picks the method.

Methods:
  1. LEXICAL      — keyword coverage (current live term). Fast, free, synonym-blind.
  2. COSINE       — cosine(embed(request), embed(narration)). Fast, but questions and
                    answers are naturally worded differently, so relevant pairs may
                    score low (the noise that got this dropped before).
  3. REVERSE-Q    — cosine(embed(request), embed(LLM_reconstructed_question)). The
                    n8n/RAGAS trick: normalise to question-vs-question. Accurate but
                    needs an LLM call (so: offline only).

Run on your Mac (has sentence-transformers; set OPENAI_API_KEY for method 3):
    PYTHONPATH=. python3 tests/test_relevance_methods.py

What to look for: which method gives a clean GAP between the relevant rows (should
score HIGH) and the irrelevant rows (should score LOW). A method is usable live if
that gap is wide AND it needs no LLM call (methods 1 and 2).
"""
from __future__ import annotations
import os, sys
import dotenv

dotenv.load_dotenv()
sys.path.insert(0, ".")

# labeled cases: (label, request, narration, is_relevant)
CASES = [
    ("arrive→arrive (exact)",      "when will my order O1001 arrive?",
     "It's expected to arrive on 2026-08-08.", True),
    ("arrive→delivered (synonym)", "when will my order arrive?",
     "It'll be delivered on Tuesday.", True),
    ("status match",               "where is my order?",
     "It is currently processing.", True),
    ("cost→price (synonym)",       "how much does the replacement cost?",
     "The replacement is priced at $49.", True),
    ("warranty asked, order given","is my device under warranty?",
     "Your device has shipped and is on its way.", False),
    ("totally off-topic",          "when will it arrive?",
     "You ordered a HeartBug Monitor and Replacement Leads.", False),
    ("cancel asked, status given", "can you cancel my order?",
     "Your order is currently in transit.", False),
    ("list orders", "I was wondering about my order, specifically the one I requested recently, could you tell me when it will arrive so I can plan accordingly?",
     "Your HeartBug Monitor was delivered on July 8, your Electrode Stickers shipped and are due by July 30, and your Replacement Device is still processing for August 2.", True)
]

# ── method 1: lexical (reuse the live term) ──
from evals.faithfulness import request_uncovered
def lexical_relevance(req, narr):
    return 1.0 - request_uncovered(req, narr)   # coverage = 1 - uncovered

# ── method 2 & 3 need embeddings ──
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _m = SentenceTransformer("all-MiniLM-L6-v2")
    def _cos(a, b):
        va, vb = _m.encode([a, b])
        return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))
    HAVE_EMB = True
except Exception as e:
    HAVE_EMB = False
    print(f"(no sentence-transformers: {e} — methods 2 & 3 skipped)\n")

def cosine_relevance(req, narr):
    return _cos(req, narr)

# ── method 3: reverse-question (LLM reconstructs the question, then cosine) ──
def reverse_q_relevance(req, narr):
    from openai import OpenAI
    client = OpenAI()
    model = os.getenv("HEARTBUG_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model, temperature=0,
        messages=[
            {"role": "system", "content":
             "Given an assistant's answer, write the single most likely user question "
             "it was answering. Reply with only the question."},
            {"role": "user", "content": narr},
        ])
    reconstructed = (resp.choices[0].message.content or "").strip()
    return _cos(req, reconstructed), reconstructed


def run():
    have_llm = bool(os.getenv("OPENAI_API_KEY")) and HAVE_EMB
    hdr = f"{'case':<32} {'relevant':>8} {'lexical':>8} {'cosine':>8}"
    if have_llm:
        hdr += f" {'reverse_q':>10}"
    print(hdr)
    print("-" * len(hdr))
    for label, req, narr, is_rel in CASES:
        lex = lexical_relevance(req, narr)
        row = f"{label:<32} {('REL' if is_rel else 'irr'):>8} {lex:>8.2f}"
        if HAVE_EMB:
            row += f" {cosine_relevance(req, narr):>8.2f}"
            if have_llm:
                rq, _ = reverse_q_relevance(req, narr)
                row += f" {rq:>10.2f}"
        print(row)
    print("\nA method works if RELEVANT rows score HIGH and irrelevant rows score LOW,")
    print("with a clean gap between. Lexical & cosine are live-safe (no LLM);")
    print("reverse_q needs an LLM call (offline). Pick the live method with the")
    print("widest relevant-vs-irrelevant gap.")


if __name__ == "__main__":
    run()
