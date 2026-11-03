"""Start the subagent for one attempt, and hand the worker its briefing.

The only state is the briefing, kept in the shared cache between the host
(place_call) and the worker (load_briefing). Keyed by callback AND attempt token,
so a stale worker can't read a newer attempt's; the TTL bounds PII at rest. The
LiveKit dispatch itself carries no PII — just the two ids.
"""

from __future__ import annotations

import secrets

from livekit import api

from src.shared.cache import cache_key, get_cache
from src.shared.outbound_contract import AGENT_NAME, Briefing, DispatchMetadata

BRIEFING_TTL_S = 30 * 60


def _key(callback_id: str, attempt_token: str) -> str:
    return cache_key("outbound_briefing", callback_id, attempt_token)


def load_briefing(callback_id: str, attempt_token: str) -> Briefing | None:
    raw = get_cache().get(_key(callback_id, attempt_token))
    return Briefing.from_dict(raw) if raw else None


def release_briefing(callback_id: str, attempt_token: str) -> None:
    get_cache().delete(_key(callback_id, attempt_token))


async def place_call(briefing: Briefing, attempt_token: str, lk: api.LiveKitAPI | None = None) -> str:
    """Store the briefing and start the worker in a fresh room. Returns the room name."""
    room = f"callback-{briefing.callback_id}-{secrets.token_hex(3)}"
    get_cache().set(_key(briefing.callback_id, attempt_token), briefing.to_dict(),
                    ttl_seconds=BRIEFING_TTL_S)
    meta = DispatchMetadata(callback_id=briefing.callback_id, attempt_token=attempt_token)
    request = api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room, metadata=meta.to_json())
    try:
        if lk is not None:
            await lk.agent_dispatch.create_dispatch(request)
        else:
            async with api.LiveKitAPI() as client:    # LIVEKIT_URL / _API_KEY / _API_SECRET
                await client.agent_dispatch.create_dispatch(request)
    except Exception:
        release_briefing(briefing.callback_id, attempt_token)   # don't leave PII behind
        raise
    return room
