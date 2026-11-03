"""Host endpoints the outbound worker calls back into (DEMO).

  POST /outbound/callbacks/{id}/verify    → {"passed", "reason"}
  POST /outbound/callbacks/{id}/outcome   → 204

Mounted in app.py (only when OUTBOUND_API_TOKEN is set):
    app.include_router(build_outbound_router(_host.outbound,
                                             api_token=os.environ["OUTBOUND_API_TOKEN"]))

LATER (handoff to a human): POST /outbound/callbacks/{id}/handoff → ports.start_handoff
LATER (scheduled callbacks): POST /internal/callbacks/dispatch-due for the ticker
"""

from __future__ import annotations

import secrets
from typing import Any, Protocol

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status

from src.shared.outbound_contract import (
    SCHEMA_VERSION,
    AttemptResult,
    ContractError,
    VerifyResult,
)


class OutboundPorts(Protocol):
    async def verify(self, callback_id: str, attempt_token: str,
                     answers: dict[str, str]) -> dict:
        """Returns {"passed": bool}, plus "reason" only when passed."""

    async def finish_attempt(self, result: AttemptResult) -> None: ...


def build_outbound_router(ports: OutboundPorts, *, api_token: str) -> APIRouter:
    def auth(authorization: str = Header(default="")) -> None:
        if not secrets.compare_digest(authorization, f"Bearer {api_token}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    def body_v1(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if body.get("schema_version") != SCHEMA_VERSION:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported schema_version")
        if not body.get("attempt_token"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "attempt_token required")
        return body

    r = APIRouter(dependencies=[Depends(auth)])

    @r.post("/outbound/callbacks/{callback_id}/verify")
    async def verify(callback_id: str, body: dict = Depends(body_v1)) -> dict:
        answers = body.get("answers")
        if not isinstance(answers, dict) or not answers:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "answers must be a non-empty object")
        res = await ports.verify(callback_id, body["attempt_token"],
                                 {str(k): str(v) for k, v in answers.items()})
        return VerifyResult.from_dict(res).to_dict()     # contract-checked on the way out

    @r.post("/outbound/callbacks/{callback_id}/outcome", status_code=204)
    async def outcome(callback_id: str, body: dict = Depends(body_v1)) -> None:
        try:
            result = AttemptResult.from_dict(body)
        except ContractError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
        if result.callback_id != callback_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "callback_id mismatch")
        await ports.finish_attempt(result)

    return r
