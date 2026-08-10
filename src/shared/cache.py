"""
Shared cache — ONE cache system used by every domain (like one shared DB).

Global (persists across calls) + per-entry TTL. Each domain owns its own keys,
namespaced by domain + subject, so the cache is shared as a SYSTEM but entries
never collide across domains:

    patient:P002        → Patient's record        (patient domain)
    orders:P002         → P002's 28-day orders     (order domain, later)
    devices:P002        → P002's device status     (device domain, later)

The interface (get/set/delete/get_namespace) is deliberately Redis-shaped, so the
in-memory store here can be swapped for Redis by changing ONLY this file — every
caller (repositories, pre-warm) stays identical.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("heartbug")


class SharedCache:
    def __init__(self) -> None:
        # key → (value, expiry_epoch_or_None)
        self._store: Dict[str, Tuple[Any, Optional[float]]] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        """Read a key. Logs HIT/MISS here — in the cache itself, not in each
        repository — so every domain reports consistently and the logging can't be
        lost when a repository is rewritten."""
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                log.info(f"CACHE MISS {key}, query DB")
                return None
            value, expiry = hit
            if expiry is not None and expiry < time.time():
                self._store.pop(key, None)          # lazy expiry
                log.info(f"CACHE EXPIRED {key}, query DB")
                return None
            log.info(f"CACHE HIT {key}")
            return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        expiry = (time.time() + ttl_seconds) if ttl_seconds else None
        with self._lock:
            self._store[key] = (value, expiry)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def get_namespace(self, prefix: str) -> Dict[str, Any]:
        """All live entries whose key starts with prefix (e.g. 'orders:P002')."""
        now = time.time()
        out: Dict[str, Any] = {}
        with self._lock:
            for k, (v, exp) in list(self._store.items()):
                if not k.startswith(prefix):
                    continue
                if exp is not None and exp < now:
                    self._store.pop(k, None)
                    continue
                out[k] = v
        return out

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# the single shared instance (one cache system, like one DB connection)
_CACHE: Optional["SharedCache"] = None


# ── serialization: JSON, but dataclasses round-trip losslessly ───────────────
# Repositories cache domain dataclasses (Order, Patient, MonitoringStatus…) and
# lists of them. Redis stores strings, so we tag each dataclass with its type on
# the way out and rebuild the exact class on the way in — no per-repository
# conversion, no registry to maintain. JSON (not pickle) keeps entries readable in
# redis-cli, which is worth the small overhead when debugging cache behaviour.
import dataclasses
import importlib
import json


def _encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        return {"__dc__": f"{cls.__module__}:{cls.__qualname__}",
                "fields": {k: _encode(v) for k, v in dataclasses.asdict(value).items()}}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and "__dc__" in value:
        module, qualname = value["__dc__"].split(":")
        cls = getattr(importlib.import_module(module), qualname)
        return cls(**{k: _decode(v) for k, v in value["fields"].items()})
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items()}
    return value


class RedisCache(SharedCache):
    """Same interface as SharedCache, backed by shared Redis so a warm entry is
    visible to every replica of a domain (an in-process dict is not). Each domain
    still reads/writes ONLY its own namespace; isolation between domains is enforced
    by per-domain Redis ACL users, not by convention."""

    def __init__(self, url: str) -> None:
        import redis                                   # optional dep, imported here
        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> Optional[Any]:
        raw = self._r.get(key)
        if raw is None:
            log.info(f"CACHE MISS {key}, query DB")     # Redis TTL = transparent expiry
            return None
        log.info(f"CACHE HIT {key}")
        return _decode(json.loads(raw))

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        self._r.set(key, json.dumps(_encode(value)), ex=ttl_seconds or None)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def set_if_absent(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        """Atomic acquire — the real one-pending-order lock. Two concurrent submits
        race on SET NX and exactly one wins; the loser is told an order is already
        pending. In-process this was a single-replica illusion; here it holds across
        replicas. Returns True iff WE acquired the slot."""
        return bool(self._r.set(key, json.dumps(_encode(value)),
                                nx=True, ex=ttl_seconds or None))

    def get_namespace(self, prefix: str) -> Dict[str, Any]:
        # SCAN, not KEYS — never block Redis. Used rarely (debug/admin), not on the
        # hot path, so the extra round trips are fine.
        out: Dict[str, Any] = {}
        for key in self._r.scan_iter(match=f"{prefix}*"):
            raw = self._r.get(key)
            if raw is not None:
                out[key] = _decode(json.loads(raw))
        return out

    def clear(self) -> None:
        self._r.flushdb()


def set_if_absent(self, key, value, ttl_seconds=None) -> bool:
    """In-process fallback: NOT atomic across processes (single-replica only). The
    interface matches RedisCache so callers don't branch; the guarantee differs, and
    that difference is exactly why the pending lock wants Redis in production."""
    with self._lock:
        if self._store.get(key) is not None:
            return False
        self.set(key, value, ttl_seconds)
        return True

SharedCache.set_if_absent = set_if_absent


def get_cache() -> SharedCache:
    """Redis when REDIS_URL is set, in-process otherwise — same switch style as the
    session store and tracing. Built once per process and reused."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    url = __import__("os").getenv("REDIS_URL")
    if url:
        try:
            cache = RedisCache(url)
            cache._r.ping()                            # fail fast, not mid-call
            log.info(f"CACHE redis → {url}")
            _CACHE = cache
            return _CACHE
        except Exception as e:                         # unreachable, missing dep…
            log.warning(f"CACHE redis unavailable ({e}) → in-process")
    _CACHE = SharedCache()
    return _CACHE


# ── key helpers — ONE place that defines the key structure ──
def cache_key(domain: str, subject_id: str) -> str:
    """Entity key: '<entity>:<its own id>' — e.g. 'patient:P002', 'order:O1003',
    'doctor:D002', 'clinic:C02'. One entity per key, keyed by ITS OWN id, so a
    write invalidates exactly one thing."""
    return f"{domain}:{subject_id}"


def index_key(entity: str, owner_kind: str, owner_id: str) -> str:
    """Index key: the LIST of entity ids belonging to an owner —
    'order_by_patient:P002', 'order_by_clinic:C02'. Entities live under their own
    keys; the index only holds ids, so there is a single source of truth per
    entity and membership changes are a separate, precise write."""
    return f"{entity}_by_{owner_kind}:{owner_id}"


def pending_key(owner_id: str) -> str:
    """The caller's outstanding submit-for-approval order. Presence = blocked from
    ordering again. Owner is the patient_id (self-orders) or clinic_id (restocks),
    always taken from verified identity — never caller-asserted. Cleared by the
    transformation server's approval webhook, with a TTL so a lost webhook can
    never lock a caller out permanently."""
    return f"pending:{owner_id}"