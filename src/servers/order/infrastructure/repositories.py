"""
Order infrastructure — concrete repository with the shared cache in front.

Cache structure (key helpers live in src/shared/cache.py):

    order:<order_id>            → ONE order (the entity, single source of truth)
    order_by_patient:<P00x>     → [order_id, ...]  a patient's own orders
    order_by_clinic:<C0x>       → [order_id, ...]  a clinic's restocks

Entities are keyed by their OWN id, so a change to one order invalidates exactly
one key. Lists are INDEXES of ids only — no second copy of the order — so a list
and a single-order lookup can never disagree.

Clinic restocks are indexed by CLINIC, not by the ordering doctor: a restock ships
to the clinic and belongs to it, so every doctor at that clinic can see it. Who
submitted it is an attribute on the order, not a separate index.

The domain service never knows caching exists — it lives behind this interface.
"""

from __future__ import annotations

from typing import List, Optional

from src.servers.order.domain.models import Order
from src.servers.order.domain.repository.interfaces import OrderRepository
from src.servers.order.infrastructure.adaptors import OrderDataAdaptor
from src.shared.cache import get_cache, cache_key, index_key

# 28-day monitoring window (Sri's caching strategy) expressed as seconds
_ORDERS_TTL = 28 * 24 * 3600


class OrderRepositoryImpl(OrderRepository):
    def __init__(self, adaptor: Optional[OrderDataAdaptor] = None) -> None:
        self._adaptor = adaptor or OrderDataAdaptor()
        self._cache = get_cache()

    # ── reads: cache → data source → backfill ────────────────────────────────
    def get_by_id(self, order_id: str) -> Optional[Order]:
        key = cache_key("order", order_id)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        order = self._adaptor.fetch_by_id(order_id)
        if order:
            self._cache.set(key, order, ttl_seconds=_ORDERS_TTL)
        return order

    def list_for_patient(self, patient_id: str) -> List[Order]:
        """Index of ids → entities. On a miss we fetch once and populate BOTH the
        index and each entity, so a later single-order lookup is already warm."""
        idx = index_key("order", "patient", patient_id)
        ids = self._cache.get(idx)
        if ids is not None:
            found = [self.get_by_id(oid) for oid in ids]
            return [o for o in found if o]              # skip any evicted/missing
        orders = self._adaptor.fetch_for_patient(patient_id)
        self._cache.set(idx, [o.order_id for o in orders], ttl_seconds=_ORDERS_TTL)
        for o in orders:                                 # warm the entities too
            self._cache.set(cache_key("order", o.order_id), o, ttl_seconds=_ORDERS_TTL)
        return orders

    # ── writes: keep the cache honest ────────────────────────────────────────
    def invalidate_order(self, order_id: str) -> None:
        """Something changed about ONE order (e.g. an approval webhook)."""
        self._cache.delete(cache_key("order", order_id))

    def invalidate_owner_index(self, owner_kind: str, owner_id: str) -> None:
        """Membership changed (a new order was submitted) → drop the index so the
        next list re-reads it. Entities are untouched: they're still valid."""
        self._cache.delete(index_key("order", owner_kind, owner_id))