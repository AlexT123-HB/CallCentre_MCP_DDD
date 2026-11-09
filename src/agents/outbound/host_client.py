"""The worker's calls back to the host: verify, outcome. None per turn."""

from __future__ import annotations

import os

import httpx

from src.shared.outbound_contract import (SCHEMA_VERSION, AttemptResult, DispatchMetadata,
                                          VerifyResult)


class HostClient:
    def __init__(self, base_url: str, api_token: str, timeout_s: float = 5.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=timeout_s,
        )

    @classmethod
    def from_env(cls) -> HostClient:
        return cls(os.environ["HOST_BASE_URL"], os.environ["OUTBOUND_API_TOKEN"])

    async def _post(self, path: str, body: dict) -> httpx.Response:
        r = await self._http.post(path, json={"schema_version": SCHEMA_VERSION, **body})
        r.raise_for_status()
        return r

    async def verify(self, meta: DispatchMetadata, answers: dict[str, str]) -> VerifyResult:
        # """Send the callee's name + dob; the host checks them via the verification
        # domain. On a pass the host also returns the reason for the call."""
        # r = await self._post(
        #     f"/outbound/callbacks/{meta.callback_id}/verify",
        #     {"attempt_token": meta.attempt_token, "answers": answers},
        # )
        # return VerifyResult.from_dict(r.json())
        """Send the callee's name + dob; on a pass the host also returns the reason.
        Retried once: verifying again is harmless."""
        last_error: Exception | None = None
        for _ in range(2):
            try:
                r = await self._post(
                    f"/outbound/callbacks/{meta.callback_id}/verify",
                    {"attempt_token": meta.attempt_token, "answers": answers},
                )
                return VerifyResult.from_dict(r.json())
            except httpx.HTTPError as e:
                last_error = e
        raise last_error

    # LATER (handoff to a human):
    # async def request_handoff(self, meta: DispatchMetadata) -> None:
    #     await self._post(f"/outbound/callbacks/{meta.callback_id}/handoff",
    #                      {"attempt_token": meta.attempt_token})

    async def report_outcome(self, result: AttemptResult) -> None:
        await self._post(f"/outbound/callbacks/{result.callback_id}/outcome", result.to_dict())

    async def aclose(self) -> None:
        await self._http.aclose()
