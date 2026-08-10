"""
Order infrastructure — the STUB write-port implementation.

Stands in for the transformation server until it connects. It:
  - generates an order reference,
  - returns status 'pending' (the real API's initial state, so narration matches),
  - logs the placement as an activity,
  - does NO real persistence.

Idempotency: it remembers references it has already issued for a given key, so a
retried request in the same process returns the SAME reference (mirrors the real
write's idempotency contract, just in-memory).

Swap point: replace this class with a TransformationServerOrderWriter that issues
`POST /api/v1/fulfilment/order` — the OrderService and everything above are
untouched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.servers.order.domain.models import OrderRequest, OrderPlaced
from src.servers.order.domain.repository.interfaces import OrderWriter

_log = logging.getLogger("heartbug")


class StubOrderWriter(OrderWriter):
    def __init__(self) -> None:
        self._issued: dict = {}          # idempotency_key -> reference
        self._seq = 0

    def place(self, request: OrderRequest) -> OrderPlaced:
        # idempotency: same key → same reference, no duplicate "order"
        if request.idempotency_key and request.idempotency_key in self._issued:
            ref = self._issued[request.idempotency_key]
            _log.info(f"ORDER PLACE (idempotent replay) key={request.idempotency_key} → {ref}")
            return OrderPlaced(accepted=True, reference=ref, status="pending",
                               reason="already submitted")

        self._seq += 1
        prefix = "CR" if request.order_type == "clinic_restocking" else "PO"
        ref = f"{prefix}-{datetime.now(timezone.utc):%Y%m%d}-{self._seq:04d}"
        if request.idempotency_key:
            self._issued[request.idempotency_key] = ref

        items = ", ".join(f"{i.quantity}x {i.product}" for i in request.items) or "(no items)"
        # ACTIVITY LOG: what was submitted, for whom, by whom — the audit trail
        _log.info(f"ORDER PLACE type={request.order_type} for={request.created_for}"
                  f":{request.created_for_id} by={request.requested_by} items=[{items}]"
                  f" → {ref} (pending)")
        return OrderPlaced(accepted=True, reference=ref, status="pending",
                           reason="submitted for processing")
