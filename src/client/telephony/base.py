"""
Vendor-independent telephony logic, shared by every telephony adapter (Twilio,
Talkdesk, ...).

The insight: whatever the vendor, ONE turn is the same shape --
    (call_id, transcript, optional confidence)
      -> decide: reprompt on empty / low-confidence, else run the host turn
      -> return a small vendor-neutral TurnOutcome the adapter renders into its
         own format (Twilio TwiML, Talkdesk JSON, ...).

So this module owns the DECISIONS (what to do); each adapter owns the FORMAT
(how to say it to its vendor). That's what keeps two adapters from drifting into
copy-pasted logic. The MCP-DDD core (host.turn) is untouched by any of this.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass

_log = logging.getLogger("heartbug")

# minimum speech-recognition confidence (0.0-1.0) to accept an utterance. Below this,
# reprompt rather than run the pipeline on a likely mis-transcription. Conservative
# default -- a phone line is noisy, so don't set too high or you over-reprompt.
_MIN_CONFIDENCE = float(os.getenv("HEARTBUG_MIN_SPEECH_CONFIDENCE", "0.4"))

_REPROMPT = "Sorry, I didn't quite catch that -- could you say it again?"


@dataclass
class TurnOutcome:
    """Vendor-neutral result of one turn. The adapter renders this into its format."""
    text: str                     # what to say to the caller
    listen_again: bool = True     # capture another utterance after speaking?
    transfer: bool = False        # hand off to a human instead of looping?
    reprompt: bool = False        # this was a reprompt (empty/low-confidence), not a real turn


def should_reprompt(transcript: str, confidence: float | None) -> bool:
    """True if we should reprompt instead of running the turn: no speech, or speech
    whose confidence is present AND below the floor. A missing confidence never
    blocks a real utterance (vendors don't guarantee it)."""
    if not transcript:
        return True
    if confidence is not None and confidence < _MIN_CONFIDENCE:
        return True
    return False


async def run_turn(host, call_id: str, transcript: str,
                   confidence: float | None, vendor: str) -> TurnOutcome:
    """The shared turn decision. `host` is the app's Host; `vendor` is just a log tag.
    Returns a TurnOutcome the caller renders into its vendor format."""
    if should_reprompt(transcript, confidence):
        if transcript and confidence is not None:
            _log.info(f"[{call_id}] {vendor} low-confidence {confidence:.2f} "
                      f"< {_MIN_CONFIDENCE} -- reprompt (heard {transcript!r})")
        return TurnOutcome(text=_REPROMPT, listen_again=True, reprompt=True)

    _log.info(f"[{call_id}] {vendor} caller: {transcript!r}")
    result = await host.turn(call_id, transcript)      # -> response TEXT (the core)
    reply = result.get("tts_response", "")

    if result.get("action") == "transfer":
        return TurnOutcome(text=reply or "Let me connect you to someone who can help.",
                           listen_again=False, transfer=True)

    return TurnOutcome(text=reply, listen_again=True)


async def run_greeting(host, call_id: str, ani: str, vendor: str) -> str:
    """Shared greeting: start the session, return the greeting TEXT. Adapter renders."""
    _log.info(f"[{call_id}] {vendor} inbound from {ani}")
    greeting = await host.webhook(call_id, ani)
    return greeting.get("tts_response") or "Welcome to HeartBug."
