"""Rehearse the demo without the full stack.

A stand-in host: the real outbound router + OutboundCaller, with patient and
verification faked from env (DEMO_NAME, DEMO_DOB). Start it, start the worker,
then trigger the transfer:

    python scripts/outbound/rehearse.py
    python -m src.agents.outbound.agent dev
    curl -X POST localhost:8787/demo/transfer-now
"""

from __future__ import annotations

import logging
import os
import secrets

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from src.client.outbound_api import build_outbound_router
from src.client.outbound import OutboundCaller
from src.shared.security import AccessContext

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

NAME = os.getenv("DEMO_NAME", "Jordan Lee")
DOB_YEAR = os.getenv("DEMO_DOB", "1980-01-01")[:4]


class FakeSessions:
    def __init__(self):
        self.store = {}

    def start(self, call_id, ani):
        self.store[call_id] = {"call_id": call_id, "ani": ani, "ctx": AccessContext(call_id=call_id)}
        return self.store[call_id]

    def get(self, call_id):
        return self.store[call_id]

    def save(self, s):
        self.store[s["call_id"]] = s


class FakeClient:
    """Stands in for patient.get_my_info and verification.verify_identity."""

    async def call(self, session, domain, tool, **args):
        if tool == "get_my_info":
            return {"name": NAME}
        if tool == "verify_identity":
            name_ok = NAME.split()[-1].lower() in str(args.get("name", "")).lower()
            dob_ok = DOB_YEAR in str(args.get("dob", ""))
            ok = name_ok and dob_ok
            logging.info(f"REHEARSAL verify {args} → {ok}")
            matched = session["ctx"].matched_subject_id
            return {"verified": ok, "subject_id": matched if ok else None}
        raise ValueError(f"rehearsal has no fake for {domain}.{tool}")


caller = OutboundCaller(FakeClient(), FakeSessions())
app = FastAPI()
app.include_router(build_outbound_router(caller, api_token=os.environ["OUTBOUND_API_TOKEN"]))


@app.post("/demo/transfer-now")
async def transfer_now() -> dict:
    call_id = f"rehearsal-{secrets.token_hex(2)}"
    session = {"call_id": call_id, "ani": os.environ["OUTBOUND_DEMO_TO_NUMBER"],
               "ctx": AccessContext(call_id=call_id)}
    session["ctx"].grant("DEMO-P1", role="patient")
    reason = os.getenv("DEMO_REASON", "your question about returning the heart monitor")
    return {"callback_id": await caller.call_now(session, reason)}


if __name__ == "__main__":
    uvicorn.run(app, port=int(os.getenv("DEV_HOST_PORT", "8787")))
