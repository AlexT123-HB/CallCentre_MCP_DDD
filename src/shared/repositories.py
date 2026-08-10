"""
Shared reusable BASE repositories (the 'write the plumbing once' layer).

No domain-specific logic. Each domain's concrete repository (in its own
infrastructure/) inherits/uses these. For Patient-only, the key-value store is
enough; the timestamped collection is here ready for Order's 28-day cache later.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Generic, Optional, TypeVar, Callable, Union

T = TypeVar("T")
K = TypeVar("K")


class InMemoryKeyValueRepository(Generic[K, T]):
    """Simple key→value store with optional TTL. The hot-cache primitive."""

    def __init__(self) -> None:
        self._data: dict = {}

    def get(self, key: K) -> Optional[T]:
        hit = self._data.get(key)
        if hit is None:
            return None
        value, expiry = hit
        if expiry is not None and expiry < time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: K, value: T, ttl_seconds: Optional[int] = None) -> None:
        expiry = (time.time() + ttl_seconds) if ttl_seconds else None
        self._data[key] = (value, expiry)

    def delete(self, key: K) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class TimestampedCollectionRepository(Generic[T, K]):
    """
    Collections of timestamped items grouped by key, with time-window queries.
    Ready for Order's 28-day window (key=patient_id). Included now so the base is
    in place when Order is added.
    """

    def __init__(self, max_items_per_key: int = 50) -> None:
        self._collections: dict = {}
        self._max = max_items_per_key

    def save(self, key: K, item: T) -> T:
        bucket = self._collections.setdefault(key, [])
        bucket.insert(0, item)
        del bucket[self._max:]
        return item

    def save_many(self, key: K, items: list) -> None:
        for it in reversed(items):
            self.save(key, it)

    def find_within_window(self, key: K, window: Union[int, timedelta] = 28,
                           unit: str = "days", limit: Optional[int] = None,
                           timestamp_getter: Callable[[T], datetime] = lambda x: x["created_at"]) -> list:
        bucket = self._collections.get(key, [])
        if not bucket:
            return []
        cutoff = self._cutoff(window, unit)
        out = []
        for item in bucket:
            ts = timestamp_getter(item)
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts >= cutoff:
                out.append(item)
        return out[:limit] if limit else out

    def find_all(self, key: K) -> list:
        return list(self._collections.get(key, []))

    @staticmethod
    def _cutoff(window: Union[int, timedelta], unit: str) -> datetime:
        if isinstance(window, timedelta):
            return datetime.now() - window
        if unit == "days":
            return datetime.now() - timedelta(days=window)
        if unit == "hours":
            return datetime.now() - timedelta(hours=window)
        raise ValueError(f"unit must be 'days' or 'hours', got {unit!r}")
