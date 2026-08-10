"""
Host narration — caller-facing speech from STRUCTURED FACTS via the LLM.

The event descriptions are ASSEMBLED from domain manifests (each domain
contributes its own events) plus host-level events (handoff, out_of_scope) that
belong to the host, not any domain. Adding a domain needs NO edit here — its
events travel with its manifest.
"""

from __future__ import annotations

from src.client.providers.interfaces import LLMProvider
from src.servers.manifests import all_manifests
from src.shared import tracing

# events owned by the HOST (not any single domain)
_HOST_EVENTS = {
    "handoff": ("tell the caller you'll connect them to a human member of the team now "
                "(mention the queue/team if given) and to please hold — do NOT ask for "
                "name or date of birth here"),
    "out_of_scope": "politely decline, you only handle HeartBug account matters",
    "small_talk": "reply warmly and briefly to the caller's social remark, then offer "
                  "to help with their HeartBug account — one short, natural sentence",
    "multi": ("You are answering a request that had several parts. The facts contain "
              "'results' (parts that succeeded) and 'failures' (parts that could NOT be "
              "answered). Give the successful results naturally. Then — this is REQUIRED — "
              "for EVERY item in 'failures', explicitly tell the caller that part could not "
              "be answered (name what it was) and offer to try again or connect them to "
              "someone. NEVER silently omit a failed part; the caller asked about it and "
              "must hear that it wasn't resolved."),
}


def _all_events() -> dict:
    """Every event → instruction, assembled from all domain manifests + host events."""
    events = dict(_HOST_EVENTS)
    for m in all_manifests():
        events.update(m.events)
    return events


_BASE_STYLE = (
    "You are a warm, polite, efficient HeartBug healthcare support agent on a phone "
    "call. Reply in one short spoken sentence. Speak naturally; never read out raw "
    "fields, IDs, or JSON verbatim — weave them into natural speech. Stay calm and "
    "confident; apologize only for a real problem, not by reflex. The product is "
    "called HeartBug — always use the correct name and never adopt an incorrect one. "
    "Respond in clear English only."
)

_EVENTS = _all_events()


def _style_for(event: str) -> str:
    """Base voice + ONLY the relevant event's instruction. Passing all ~30 event
    descriptions (4kB) buries the specific facts for a small model, producing
    generic narration; a focused instruction keeps the facts front and centre."""
    instruction = _EVENTS.get(event)
    if instruction:
        return (_BASE_STYLE + f"\n\nThis turn's situation ({event}): {instruction}\n"
                "Convey the specific facts you are given for this situation.")
    return _BASE_STYLE


# kept for backwards-compat / tests that import it
NARRATION_STYLE = _BASE_STYLE + " The 'event' field in the facts tells you the "\
    "situation: " + "".join(f"{k}={v}; " for k, v in _EVENTS.items())


class Narrator:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def say(self, facts: dict, caller_said: str = "", history: list = None) -> str:
        # focus the prompt on THIS event only, so the facts aren't buried
        event = facts.get("event", "")
        style = _style_for(event)
        #return self._provider.narrate(style, facts, caller_said)

        # records WHICH event and WHICH facts produced the wording — this is what
        # explains "why did it say that?" without re-reading prompts by hand.
        with tracing.span("narrate", tracing.CHAIN, event=event, facts=facts) as sp:
            tts = self._provider.narrate(style, facts, caller_said, history or [])
            sp.set(tts=tts)
            return tts
        
    def answer_from_context(self, caller_said: str, history: list) -> str:
        """Answer a follow-up from the conversation so far — no tool, no facts dict.
        Used when the caller asks about something already established this call."""
        with tracing.span("answer_from_context", tracing.CHAIN, caller_said=caller_said) as sp:
            tts = self._provider.answer_from_context(_BASE_STYLE, caller_said, history)
            sp.set(tts=tts)
            return tts
 

