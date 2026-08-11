"""
faithfulness.py — a VAE-structured loss signal for detecting Mode-B hallucination.

The idea (borrowed from the VAE loss STRUCTURE, not its architecture): a good
narration has two complementary fidelities, and BOTH are required —

  • RECONSTRUCTION term  (tool_result → narration):
        is the narration faithful to the tool results? i.e. is everything it
        says supported by what the tools actually returned? A low score here
        means the model narrated something the tools didn't support — a
        hallucination. This is the grounding/faithfulness term.

  • REGULARIZATION term  (request → narration):
        does the narration actually address what the caller asked? A perfectly
        grounded narration that answers the WRONG question fails this term. This
        keeps the output "in distribution" w.r.t. the caller's intent.

The dangerous cell is HIGH regularization + LOW reconstruction: an on-topic answer
that is NOT grounded in the tools — a confident, relevant-sounding fabrication,
which is exactly what context-rot produces. Neither term alone catches it; the
two-term structure isolates it.

IMPORTANT LIMITATION (why semantic similarity isn't enough on its own):
embedding similarity is blind to single-value swaps — "shipped Aug 4" vs
"shipped Aug 8" are near-identical vectors but one is fabricated. Those wrong-value
hallucinations are the dangerous ones here (a wrong date, an invented order #). So
the reconstruction term PAIRS semantic similarity with a DETERMINISTIC value check:
extract numbers / dates / IDs from the narration and confirm each appears in the
tool results. Semantic catches topic drift; the value check catches value fabrication.

This module is PURE MEASUREMENT — it computes a loss in [0,1]; it does not decide
anything. The mode router's AIMD loop consumes `turn_bad = loss > threshold`.

Weights: reconstruction dominates (a hallucinated clinical fact is worse than mild
relevance drift), mirroring the AIMD fast-down/slow-up asymmetry.
"""
from __future__ import annotations

import os
import re
from typing import Any

# ── embedding model (reuse the same one KB uses; lazy, optional) ──────────────
_EMBED_MODEL_NAME = os.environ.get("KB_EMBED_MODEL", "all-MiniLM-L6-v2")
_embed_model = None


def _model():
    """Lazy-load the sentence-transformer once. Returns None if unavailable, in
    which case the semantic terms are skipped (value-grounding still runs)."""
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
        except Exception:
            _embed_model = False   # sentinel: tried and failed
    return _embed_model or None


def _cos(a: str, b: str) -> float | None:
    """Cosine similarity of two texts in [0,1]; None if no model or empty text."""
    if not a or not b:
        return None
    m = _model()
    if m is None:
        return None
    try:
        import numpy as np
        va, vb = m.encode([a, b])
        denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
        sim = float(np.dot(va, vb) / denom)
        # cosine is [-1,1]; clamp to [0,1] (negatives are "unrelated" → 0)
        return max(0.0, min(1.0, sim))
    except Exception:
        return None


# ── deterministic value grounding (catches wrong-value fabrication) ───────────
# numbers (incl. decimals), dates (a few common shapes), and ID-like tokens
_NUM_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}"                       # 2026-08-04
    r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"          # 4/8 or 04-08-2026
    r"|\d{1,2}\s+[A-Z][a-z]+"                        # 4 August
    r"|[A-Z][a-z]+\s+\d{1,2})\b")                    # August 4
_ID_RE = re.compile(r"\b[A-Z]{1,4}[-_]?\d{3,}\b")   # O1001, PO-20260802-0001, R003
# NOTE: emails/phones are deliberately NOT extracted. Contact PII is masked/removed
# by the output rail before the narration reaches the caller, so a fabricated email
# never actually gets spoken — it's out of scope for the faithfulness loss. (An
# earlier attempt to catch them also made the phone regex swallow dates, double-
# counting them.) The loss checks DOMAIN values — numbers, dates, IDs — that the
# rail passes through and that a wrong-value hallucination corrupts.


def _values(text: str) -> set[str]:
    """Extract the checkable, fabrication-prone tokens from a text: numbers,
    dates, and IDs. These are what a wrong-value hallucination corrupts.

    Dates and IDs are extracted FIRST and their component digits removed from the
    number set, so a fabricated date like '2026-08-05' counts as ONE value, not as
    '2026' + '08' + '05' + the full string (which over-weighted the value term)."""
    if not text:
        return set()
    dates = {m.group() for m in _DATE_RE.finditer(text)}
    ids = {m.group() for m in _ID_RE.finditer(text)}
    # numbers, minus any digit-run already part of a matched date or ID
    structured = " ".join(dates | ids)
    nums = {m.group().replace(",", "") for m in _NUM_RE.finditer(text)}
    nums = {n for n in nums if n not in structured}
    return dates | ids | nums


def _tool_text(tool_results: Any) -> str:
    """Flatten tool results (dict/list/str) into one string for embedding + value
    extraction. Robust to whatever shape the turn's results arrive in."""
    if tool_results is None:
        return ""
    if isinstance(tool_results, str):
        return tool_results
    if isinstance(tool_results, dict):
        return " ".join(f"{k}: {v}" for k, v in _flatten(tool_results))
    if isinstance(tool_results, (list, tuple)):
        return " ".join(_tool_text(x) for x in tool_results)
    return str(tool_results)


def _flatten(d: dict, prefix: str = ""):
    """Yield (key, value) pairs from a nested dict, so values at any depth are
    captured for grounding."""
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flatten(v, f"{prefix}{k}.")
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    yield from _flatten(item, f"{prefix}{k}.{i}.")
                else:
                    yield f"{prefix}{k}.{i}", item
        else:
            yield f"{prefix}{k}", v


# words too common to be "key entities" — ignored when checking coverage
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "my", "your", "our", "their", "his", "her", "its", "this", "that", "these",
    "i", "you", "we", "they", "he", "she", "it", "me", "us", "them",
    "to", "of", "in", "on", "at", "for", "with", "and", "or", "but", "if",
    "can", "could", "would", "should", "will", "shall", "may", "do", "does",
    "did", "have", "has", "had", "what", "when", "where", "who", "how", "why",
    "please", "thanks", "thank", "hi", "hello", "just", "still", "about",
    "get", "got", "let", "know", "tell", "give", "want", "need", "like",
    "check", "look", "looking", "see", "find", "show", "help", "remind",
    "confirm", "there", "here", "some", "any", "also", "back", "again",
}


def _keywords(text: str) -> set[str]:
    """Content words of a text: lowercased tokens minus stopwords and pure
    punctuation. These are the 'key entities' a request is asking about."""
    if not text:
        return set()
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text.lower())
    return {t for t in toks if t not in _STOPWORDS and len(t) > 1}


# def request_unaddressed(request: str, narration: str) -> float:
#     """Regularization term: did the narration ADDRESS the request? — via embedding
#     cosine similarity, which (unlike lexical keyword overlap) handles synonyms and
#     paraphrase. Live testing on labeled pairs showed a clean gap: relevant answers
#     scored ≥0.55 cosine, irrelevant ≤0.44, so ~0.50 separates them. Lexical failed
#     here (synonym 'arrive'→'delivered' scored 0 coverage); cosine scores it 0.60.

#     Returns a mismatch in [0,1]: how far BELOW the relevance floor the similarity is,
#     rescaled. sim≥FLOOR → 0 (addressed); sim→0 → 1 (fully unaddressed). Falls back to
#     the lexical keyword measure when no embedding model is available."""
#     sim = _cos(request, narration)
#     if sim is None:                          # no embedding model → lexical fallback
#         return request_uncovered(request, narration)
#     # rescale: at/above the floor = addressed (0); below scales up to 1 at sim=0
#     if sim >= _REL_FLOOR:
#         return 0.0
#     return round((_REL_FLOOR - sim) / _REL_FLOOR, 3)


# cosine floors — the similarity at/above which the pair is "aligned with request".
# TWO floors because the pairs have different natural cosine ranges:
#   • request↔narration: both natural language → higher cosines (relevant ≥~0.55)
#   • request↔tool_result: NL question vs structured data → lower cosines by nature,
#     so its floor sits lower. Calibrate from real data with diag_relevance_floor.py.
_REL_FLOOR = 0.50           # request ↔ narration


def rel_diff(a: str, b: str, floor: float) -> float | None:
    """Request-anchored drift term. cosine(a,b) ≥ floor → 0 (aligned); below scales
    up to 1 at cosine 0 (fully drifted). Returns None if no embedding model."""
    sim = _cos(a, b)
    if sim is None:
        return None
    if sim >= floor:
        return 0.0
    return round((floor - sim) / floor, 3)


# def request_uncovered(request: str, narration: str) -> float:
#     """Regularization term: did the narration ADDRESS the request's key entities?

#     Not semantic similarity (noisy on short factual text), not label comparison
#     (intent-mapping missed narrations that followed the plan but answered the wrong
#     thing). Instead: extract the request's content words (the entities/fields the
#     caller asked about) and measure the fraction the narration FAILS to mention.

#     request:   "when will my order O1001 arrive?"  → key: {order, o1001, arrive}
#     narration: "your order O1001 shipped on Aug 4" → covers order, o1001; NOT arrive
#     → uncovered = 1/3 ≈ 0.33  (the 'arrive' the caller asked about went unaddressed)

#     This catches the case intent-mapping missed: the plan was right and executed,
#     but the spoken answer didn't address what was actually asked. Deterministic,
#     full-sentence, no embedding noise. Returns fraction uncovered in [0,1]."""
#     req_kw = _keywords(request)
#     if not req_kw:
#         return 0.0                                  # nothing to cover → no signal
#     narr_kw = _keywords(narration)
#     # a request keyword is "covered" if it appears in the narration, OR a near
#     # form does (prefix match handles arrive/arrives/arrival, ship/shipped, etc.)
#     covered = 0
#     for k in req_kw:
#         if k in narr_kw or any(n.startswith(k[:4]) or k.startswith(n[:4])
#                                for n in narr_kw):
#             covered += 1
#     return 1.0 - (covered / len(req_kw))
# weights: the DETERMINISTIC terms (value, intent) carry the signal; the semantic
# term is a small tiebreaker only. Rationale from live testing: on short factual
# voice narrations grounded in structured tool results, embedding similarity is
# noisy — a grounded answer scores low just because its phrasing differs from the
# tool result (e.g. "arrives on 2026-08-08" vs "eta: 2026-08-08"). That produced
# false positives on clean turns. The value + intent terms caught every real
# failure with zero false positives, so they dominate; semantic is kept small so
# it can refine but never alone push a clean turn over the escalation trigger.
# Two deterministic terms carry the whole signal — the semantic tiebreak was
# removed after live testing showed it caught NO real failure the deterministic
# terms missed and only added baseline noise on clean turns (a grounded short
# answer like "arrives 2026-08-08" scores low against "eta: 2026-08-08" purely on
# phrasing). Dropping it makes clean turns score ~0 and removes the embedding dep.
_W_TOOL = 0.50    # diff(request, tool_result) — did selection fetch relevant data?
_W_NARR = 0.50    # diff(request, narration)   — did the answer address the request?


def faithfulness_loss(request: str, narration: str, tool_results: Any,
                      **_ignored) -> dict:
    """Two-term loss for one Mode-B turn:

        loss = diff(request,     narration)   # did the answer address the request?
             + diff(tool_result, request)     # did selection fetch data for the request?

    Same structure as before; the only change is the second term's reference:
    diff(tool_result, narration) → diff(tool_result, request). The old term compared
    the tool result to the narration, which was blind to selection rot (the narration
    stays faithful to whatever tool ran). Comparing the tool result to the REQUEST
    instead exposes when the wrong data was fetched for what the caller asked.

    Both terms are cosine-based (rel_diff), single floor. Label-free, live-safe."""
    tool_text = _tool_text(tool_results)

    d_narr = rel_diff(request, narration, _REL_FLOOR)    # diff(request, narration)
    d_tool = rel_diff(tool_text, request, _REL_FLOOR)    # diff(tool_result, request)

    terms = []
    if d_narr is not None:
        terms.append((_W_NARR, d_narr, "diff_request_narration"))
    if d_tool is not None:
        terms.append((_W_TOOL, d_tool, "diff_tool_request"))

    if not terms:                                        # no embedding model
        return {"loss": 0.0, "diff_narr": None, "diff_tool": None,
                "components": {}, "note": "no embedding model"}

    wsum = sum(w for w, _, _ in terms) or 1.0
    loss = sum(w * v for w, v, _ in terms) / wsum

    return {
        "loss": round(loss, 3),
        "diff_narr": None if d_narr is None else round(d_narr, 3),
        "diff_tool": None if d_tool is None else round(d_tool, 3),
        "components": {name: round(v, 3) for _, v, name in terms},
        "tool_text": tool_text,
    }