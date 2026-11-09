"""
FastAPI app — the host's HTTP surface.

Endpoints:
  POST /webhook        body: {call_id, ani}              → greeting
  POST /{domain}       body: {call_id, transcript}       → one turn, routed to a domain
       e.g. POST /verification   {call_id, transcript}
            POST /patient        {call_id, transcript}
       The path domain is a HINT; the LLM router + gate decide the actual domain.

Run:
    pip install fastapi uvicorn openai
    export OPENAI_API_KEY=...
    uvicorn src.client.app:app --port 8000
"""

from __future__ import annotations

import os

from src.client.outbound_api import build_outbound_router
from fastapi import FastAPI
from pydantic import BaseModel

from src.client.host import Host

app = FastAPI(title="HeartBug DDD Host")
_host = Host()
# Twilio telephony adapter — adds /twilio/voice, /twilio/turn.
# It translates Twilio's webhook+TwiML format onto _host.webhook()/turn(); the
# MCP-DDD core is unchanged. Share THIS _host so there's one instance.
from src.client.telephony import twilio_adapter
twilio_adapter._host = _host          # reuse the same host, don't build a second
app.include_router(twilio_adapter.router)

# Talkdesk telephony adapter — adds /talkdesk/voice, /talkdesk/turn.
# Thin JSON data-dip endpoints the Talkdesk Studio flow calls per turn (Talkdesk
# owns the turn-loop + STT/TTS in Studio; we just return reply text). Same shared
# host and same shared decision logic (telephony base) as Twilio.
from src.client.telephony import talkdesk_adapter
talkdesk_adapter._host = _host
app.include_router(talkdesk_adapter.router)

# Outbound call subagent — adds /outbound/callbacks/{id}/verify|handoff|outcome,
# which the outbound worker container calls back into. Same shared host. Mounted
# only when OUTBOUND_API_TOKEN is set, so the app still starts without outbound.
from src.client.outbound_api import build_outbound_router
if os.getenv("OUTBOUND_API_TOKEN"):
    app.include_router(build_outbound_router(_host.outbound,
                                             api_token=os.environ["OUTBOUND_API_TOKEN"]))


class WebhookBody(BaseModel):
    call_id: str
    ani: str


class TurnBody(BaseModel):
    call_id: str
    transcript: str


@app.post("/webhook")
async def webhook(body: WebhookBody):
    return await _host.webhook(body.call_id, body.ani)

@app.get("/")              # ← ADD HERE (before /{domain})
async def health():
    return {"status": "ok"}


@app.post("/{domain}")
async def domain_turn(domain: str, body: TurnBody):
    # `domain` is a routing hint; the LLM router + gate govern the real choice.
    return await _host.turn(body.call_id, body.transcript, domain_hint=domain)
