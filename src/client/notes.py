"""
Call notes — the call's structured working memory.

The conversation history is windowed (only the last few turns reach the model), so
anything said early scrolls out: by turn 10 the order list read out at turn 2 is
gone, and "when will it arrive?" has nothing to resolve against. The note fixes
that: a compact block that lives OUTSIDE the rolling window and is injected in full
on every turn, so the entities and actions of this call are always in view.

Two deliberate choices:

  * It is written IN CODE from tool results, not by asking the model to summarise.
    Ids and statuses are copied verbatim (no drift), and it costs no extra LLM call
    — latency matters on a voice line.

  * It is a MEMORY AID, NEVER AN AUTHORISATION. An order id appearing here does not
    mean the caller may read it; every tool call still re-enforces ownership at the
    domain server. Same discipline as `matched_subject_id` in verification.

Bounded on purpose — an unbounded note becomes the context bloat it exists to avoid.
"""

from __future__ import annotations

from typing import Any, Optional

MAX_ENTITIES = 10
MAX_ACTIONS = 5

# Fields worth remembering about an entity, in the order they read naturally.
# Anything else in a tool result is left out — the note is a summary, not a copy.
_INTERESTING = ("product", "name", "status", "carrier", "tracking", "eta",
                "clinic", "address", "reference")


def _blank() -> dict:
    return {"entities": [], "actions": []}


def _entity_id(item: dict) -> Optional[str]:
    """How the CALLER would refer to this thing.

    Only customer-facing identifiers: order numbers and references are things a
    caller says out loud, so they belong here. Internal database keys (patient_id,
    doctor_id, clinic_id) deliberately do NOT — nobody rings up and says "P002".
    They add nothing the model can use for reference resolution, they are needless
    data to send across the LLM boundary in a PHI system, and having them in a
    prominent block invites the model to read one out ("checking patient P003").

    For people, the caller's vocabulary is their NAME, so that is what we key on.
    """
    for key in ("order_id", "reference"):
        if item.get(key):
            return str(item[key])
    return str(item["name"]) if item.get("name") else None


def _summarise(item: dict) -> dict:
    """Project a tool result down to {caller-facing id + a few descriptive fields}.
    Anything not listed in _INTERESTING is dropped — the note is a summary, not a
    copy of the record, which also keeps internal fields out by default."""
    eid = _entity_id(item)
    return {"id": eid,
            **{k: item[k] for k in _INTERESTING
               if item.get(k) and str(item[k]) != eid}}   # don't repeat the key


def record_result(session: dict, tool: str, result: dict) -> None:
    """Note the entities a successful tool call surfaced. Called at the single
    point where tool results land, so every domain is covered automatically."""
    if not isinstance(result, dict):
        return
    note = session.setdefault("note", _blank())

    # a list result (e.g. list_orders) carries many entities; a single lookup, one
    items = result.get("orders") or result.get("alerts") or []
    if not isinstance(items, list):
        items = []
    if not items and _entity_id(result):
        items = [result]

    for item in items:
        if not isinstance(item, dict):
            continue
        eid = _entity_id(item)
        if not eid:
            continue
        summary = _summarise(item)
        # re-noting an entity REPLACES the old line — a status may have changed,
        # and two lines for one order would be worse than none
        note["entities"] = [e for e in note["entities"] if e.get("id") != eid]
        note["entities"].append(summary)

    note["entities"] = note["entities"][-MAX_ENTITIES:]


def record_action(session: dict, description: str) -> None:
    """Note something the system DID this call (submitted, cancelled, verified).
    Lets a later 'did that go through?' be answered from what we know, instead of
    a lookup that finds nothing because it was cancelled before it was ever sent."""
    note = session.setdefault("note", _blank())
    note["actions"].append(description)
    note["actions"] = note["actions"][-MAX_ACTIONS:]


def render(session: dict) -> str:
    """The note as a compact block for the prompt. Empty string when there's
    nothing worth saying — never inject an empty heading."""
    note = session.get("note") or {}
    entities, actions = note.get("entities") or [], note.get("actions") or []
    if not entities and not actions:
        return ""

    lines = ["CALL NOTES (facts already established this call):"]
    for e in entities:
        detail = " · ".join(f"{v}" for k, v in e.items() if k != "id")
        lines.append(f"  {e['id']}  {detail}" if detail else f"  {e['id']}")
    for a in actions:
        lines.append(f"  ACTION: {a}")
    return "\n".join(lines)
