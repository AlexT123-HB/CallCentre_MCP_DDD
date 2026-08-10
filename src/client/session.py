"""
Host-side session store — per-call state, keyed by call_id.

Holds the AccessContext (verification/role), the conversation history and the call
note. Two implementations behind one interface:

  SessionStore       in-process dict — used when REDIS_URL is unset (local runs,
                     tests). State dies with the process.
  RedisSessionStore  one Hash per call with a TTL. Survives a container restart
                     mid-call, and lets ANY host replica serve ANY turn — the host
                     stops being the thing that has to stay up.

The session dict is mutated in place all over the host (history.append, ctx.grant,
note updates). Rather than sprinkle save() calls at every mutation, the host does
ONE load at the start of a turn and ONE save at the end — see Host.turn(). The
mutations in between are untouched.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Dict, Optional

from src.shared.security import AccessContext

log = logging.getLogger("heartbug")

# A call is minutes long; the TTL only needs to outlive an unusually slow one and
# then clean up after itself. No reaper, no bookkeeping — Redis expires it.
SESSION_TTL = int(os.getenv("HEARTBUG_SESSION_TTL", str(2 * 3600)))


def _new_session(call_id: str, ani: str) -> dict:
    return {"call_id": call_id, "ani": ani,
            "ctx": AccessContext(call_id=call_id), "history": []}


class SessionStore:
    """In-process store. The interface every other implementation matches."""

    def __init__(self) -> None:
        self._sessions: Dict[str, dict] = {}

    def start(self, call_id: str, ani: str) -> dict:
        session = _new_session(call_id, ani)
        self._sessions[call_id] = session
        return session

    def get(self, call_id: str) -> dict:
        return self._sessions[call_id]

    def has(self, call_id: str) -> bool:
        return call_id in self._sessions

    def save(self, session: dict) -> None:
        """No-op: callers already hold the live object."""


class RedisSessionStore(SessionStore):
    """One Hash per call at `session:<call_id>`, with a TTL refreshed on write.

    Serialised as JSON, not pickle, so a session is readable straight from
    redis-cli while debugging — worth more than the few bytes it costs. Only
    `ctx` needs conversion; everything else is already JSON-native.
    """

    def __init__(self, url: str) -> None:
        super().__init__()
        import redis                                  # imported here: optional dep
        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._key = lambda cid: f"session:{cid}"

    # ── (de)serialisation ────────────────────────────────────────────────────
    # Two object types live in the session and need converting; everything else
    # (history, note, counters) is already JSON-native.
    @staticmethod
    def _dump(session: dict) -> dict:
        out = {k: v for k, v in session.items() if k not in ("ctx", "_slots")}
        slots = {k: {"domain": s.domain, "tool": s.tool, "values": s.values}
                 for k, s in (session.get("_slots") or {}).items()}
        return {"ctx": json.dumps(asdict(session["ctx"])),
                "slots": json.dumps(slots),
                "data": json.dumps(out)}

    @staticmethod
    def _load(raw: dict) -> dict:
        from src.client.slots import SlotState
        session = json.loads(raw["data"])
        session["ctx"] = AccessContext(**json.loads(raw["ctx"]))
        slots = {}
        for key, s in json.loads(raw.get("slots") or "{}").items():
            state = SlotState(s["domain"], s["tool"])
            state.values = s["values"]          # mid-fill args survive the restart
            slots[key] = state
        if slots:
            session["_slots"] = slots
        return session

    # ── store interface ──────────────────────────────────────────────────────
    def start(self, call_id: str, ani: str) -> dict:
        session = _new_session(call_id, ani)
        self.save(session)
        return session

    def get(self, call_id: str) -> dict:
        raw = self._r.hgetall(self._key(call_id))
        if not raw:
            raise KeyError(call_id)                   # same shape as the dict store
        return self._load(raw)

    def has(self, call_id: str) -> bool:
        return bool(self._r.exists(self._key(call_id)))

    def save(self, session: dict) -> None:
        """Write the whole session back and refresh its TTL. HSET and EXPIRE go in
        one transaction so a dropped connection between them can't leave a session
        key with no expiry — an immortal key holding PHI is the bad outcome."""
        key = self._key(session["call_id"])
        pipe = self._r.pipeline()
        pipe.hset(key, mapping=self._dump(session))
        pipe.expire(key, SESSION_TTL)
        pipe.execute()


def build_session_store() -> SessionStore:
    """Redis when REDIS_URL is set, in-process otherwise. Same switch style as
    tracing: one env var to fall back if Redis misbehaves."""
    url = os.getenv("REDIS_URL")
    if not url:
        return SessionStore()
    try:
        store = RedisSessionStore(url)
        store._r.ping()                                # fail fast, not mid-call
        log.info(f"SESSIONS redis → {url}")
        return store
    except Exception as e:                             # unreachable, missing dep…
        log.warning(f"SESSIONS redis unavailable ({e}) → in-process store")
        return SessionStore()