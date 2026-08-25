"""
Talkdesk adapter -- a thin JSON endpoint the Talkdesk Studio flow calls per turn.

Talkdesk is NOT like Twilio. Talkdesk runs the call through a Studio flow (its
visual designer) that does STT (Voice Transcription component) and TTS (Text-to-
speech / Amazon Polly component). At the point where the flow needs our AI, it does
a Connections "Execute Action" data dip to THIS endpoint with the transcript, and
binds our returned reply to a variable that a TTS component then speaks. The loop
(transcribe -> call us -> speak -> transcribe) lives in the Studio flow, not here.

So our side is deliberately tiny and stateless: receive {call_id, transcript},
run the shared turn, return {reply, listen_again, transfer} as JSON. All the
decision logic is in telephony_base (shared with Twilio); this file owns only the
Talkdesk request/response FORMAT.

Setup (Talkdesk side, in their UI -- see docs/TELEPHONY.md):
  1. Request Talkdesk API access + create an OAuth client (Builder).
  2. Create a Connection (base URL = this service, OAuth auth).
  3. Create an Action = POST /talkdesk/turn with body {call_id, transcript}.
  4. Build a Studio inbound flow: Inbound Voice -> Voice Transcription ->
     Execute Action (the data dip) -> Text-to-speech (speak {{reply}}) -> loop
     back to Voice Transcription unless {{transfer}} is true.

Auth: the data dip is authenticated by Talkdesk's Connection (OAuth) on ITS side;
optionally verify a shared secret header here (HEARTBUG_TALKDESK_SECRET) so only
Talkdesk can call this endpoint.
"""
from __future__ import annotations

import os
import logging

from fastapi import APIRouter, Request, Response, HTTPException

from src.client.host import Host
from src.client.telephony import base

router = APIRouter(prefix="/talkdesk")
_log = logging.getLogger("heartbug")
_host = Host()                                   # app.py overwrites with its instance

# optional shared-secret guard: if set, require Talkdesk's data dip to send it.
_SECRET = os.getenv("HEARTBUG_TALKDESK_SECRET", "")


def _check_auth(request: Request) -> None:
    if _SECRET and request.headers.get("X-HeartBug-Secret") != _SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/voice")
async def voice(request: Request) -> dict:
    """Called once at call start by the Studio flow (optional). Body carries the
    call id and caller number; we start the session and return the greeting text
    for a TTS component to speak.

    Expected JSON in (bind these from Studio variables):
      {"call_id": "...", "ani": "+61..."}
    Out: {"reply": "<greeting>", "listen_again": true}
    """
    _check_auth(request)
    data = await request.json()
    call_id = str(data.get("call_id") or "")
    ani = str(data.get("ani") or "")
    greeting = await base.run_greeting(_host, call_id, ani, vendor="TALKDESK")
    return {"reply": greeting, "listen_again": True, "transfer": False}


@router.post("/turn")
async def turn(request: Request) -> dict:
    """Called per caller utterance by the Studio flow's Execute Action data dip.

    Expected JSON in (bind from Studio variables -- transcript from the Voice
    Transcription component, call_id from the flow):
      {"call_id": "...", "transcript": "...", "confidence": 0.0-1.0 (optional)}
    Out (bind these to Studio variables for the TTS + routing components):
      {"reply": "<text to speak>", "listen_again": bool, "transfer": bool}
    """
    _check_auth(request)
    data = await request.json()
    call_id = str(data.get("call_id") or "")
    transcript = str(data.get("transcript") or "").strip()
    raw_conf = data.get("confidence")
    try:
        confidence = float(raw_conf) if raw_conf is not None else None
    except (TypeError, ValueError):
        confidence = None

    outcome = await base.run_turn(_host, call_id, transcript, confidence, vendor="TALKDESK")
    return {
        "reply": outcome.text,
        "listen_again": outcome.listen_again,
        "transfer": outcome.transfer,
    }
