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

from fastapi import FastAPI
from pydantic import BaseModel

from src.client.host import Host

app = FastAPI(title="HeartBug DDD Host")
_host = Host()


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
