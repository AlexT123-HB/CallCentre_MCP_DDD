"""
Twilio adapter -- renders the shared TurnOutcome into TwiML.

Flow: caller speaks -> Twilio STT (<Gather input="speech">) POSTs the transcript
-> shared run_turn() decides the reply -> we render TwiML (<Say> = TTS, <Gather> =
listen again) -> Twilio speaks + listens. TEXT IN -> TEXT OUT; Twilio owns STT+TTS.

All the DECISION logic (reprompt, transfer, host turn) lives in telephony_base.
This file owns only the Twilio FORMAT (TwiML + webhook parsing).
"""
from __future__ import annotations

import os
import logging
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request, Response

from src.client.host import Host
from src.client.telephony import base

router = APIRouter(prefix="/twilio")
_log = logging.getLogger("heartbug")
_host = Host()                                   # app.py overwrites with its instance

_BASE = os.getenv("HEARTBUG_PUBLIC_URL", "https://your-host")
_SPEECH_MODEL = os.getenv("HEARTBUG_SPEECH_MODEL", "experimental_conversations")
_SPEECH_TIMEOUT = os.getenv("HEARTBUG_SPEECH_TIMEOUT", "3")   # integer required with a model


def _twiml(body: str) -> Response:
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
                    media_type="application/xml")


def _say_then_listen(text: str) -> str:
    """Twilio SAYS text (TTS), then LISTENS (STT) and POSTs the transcript to /turn."""
    safe = escape(text or "")
    return (
        f'<Gather input="speech" action="{_BASE}/twilio/turn" method="POST" '
        f'speechModel="{_SPEECH_MODEL}" speechTimeout="{_SPEECH_TIMEOUT}" language="en-AU">'
        f"<Say>{safe}</Say></Gather>"
        f'<Redirect method="POST">{_BASE}/twilio/turn</Redirect>'
    )


def _render(outcome: base.TurnOutcome) -> Response:
    """TurnOutcome -> TwiML."""
    safe = escape(outcome.text or "")
    if outcome.transfer:
        transfer_to = os.getenv("HEARTBUG_TRANSFER_NUMBER", "")
        # never hang up on a clinical caller if no number is set -- say the line and end gracefully
        handoff = (f"<Dial>{escape(transfer_to)}</Dial>" if transfer_to
                   else "<Say>Please hold and someone will be with you shortly.</Say>")
        return _twiml(f"<Say>{safe}</Say>{handoff}")
    return _twiml(_say_then_listen(outcome.text))


@router.post("/voice")
async def voice(request: Request) -> Response:
    """Inbound call: From = caller number, CallSid = call id."""
    form = await request.form()
    call_id = form.get("CallSid", "")
    ani = form.get("From", "")
    greeting = await base.run_greeting(_host, call_id, ani, vendor="TWILIO")
    return _twiml(_say_then_listen(greeting))


@router.post("/turn")
async def turn(request: Request) -> Response:
    """One caller utterance: SpeechResult = transcript, Confidence = 0.0-1.0 score."""
    form = await request.form()
    call_id = form.get("CallSid", "")
    transcript = (form.get("SpeechResult") or "").strip()
    try:
        confidence = float(form.get("Confidence"))
    except (TypeError, ValueError):
        confidence = None
    outcome = await base.run_turn(_host, call_id, transcript, confidence, vendor="TWILIO")
    return _render(outcome)
