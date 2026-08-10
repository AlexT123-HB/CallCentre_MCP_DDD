"""
Order service — the business logic.

The security core: a verified caller can ONLY see their own orders. Every read is
scoped to ctx.subject_id (list) or ownership-checked (lookup). A caller can never
retrieve another patient's order, even by guessing an order id.

Writes (placing orders) go through the OrderWriter PORT — a stub now, the
transformation server later. Two write paths with DIFFERENT authorization:
  - place_order   : patient self-order       (self-ownership — the caller orders for themselves)
  - place_restock : doctor clinic restock    (relationship + HUMAN-APPROVAL gate, read-oriented safety)
"""

from __future__ import annotations

from typing import List, Optional
import uuid

from src.servers.order.domain.models import Order, OrderItem, OrderPlaced, OrderRequest
from src.servers.order.domain.repository.interfaces import OrderRepository, OrderWriter
from src.servers.order.domain.service.interfaces import OrderService
from src.shared.security import AccessContext, AccessDenied


class DefaultOrderService(OrderService):
    def __init__(self, repository: OrderRepository, writer: OrderWriter) -> None:
        self._repo = repository
        self._writer = writer

    def list_orders(self, ctx: AccessContext) -> List[Order]:
        ctx.require_verified()
        # scoped to the caller's own id — never another patient's orders
        return self._repo.list_for_patient(ctx.subject_id)

    def lookup_order(self, ctx: AccessContext, order_id: str) -> Optional[Order]:
        ctx.require_verified()
        order = self._repo.get_by_id(order_id)
        if order is None:
            return None
        # ownership check: even a valid order id from another patient is refused
        if not order.belongs_to(ctx.subject_id):
            return None
        return order

    # ── writes (through the port) ────────────────────────────────────────────
    def place_order(self, ctx: AccessContext, product: str, quantity: int) -> OrderPlaced:
        """Patient self-order: the caller orders supplies for THEMSELVES."""
        ctx.require_verified()
        request = OrderRequest(
            order_type="patient_order", created_for="patient",
            created_for_id=ctx.subject_id, requested_by=ctx.subject_id,
            items=[OrderItem(product=product, quantity=quantity)],
            idempotency_key=str(uuid.uuid4()))
        return self._writer.place(request)

    def invalidate_owner_index(self, owner_kind: str, owner_id: str) -> None:
        """A new order changed WHO OWNS WHAT — drop that owner's cached list so the
        next 'what are my orders' re-reads it. Entities stay valid, so only the
        membership index is dropped."""
        invalidate = getattr(self._repo, "invalidate_owner_index", None)
        if invalidate:
            invalidate(owner_kind, owner_id)

    def invalidate_order(self, order_id: str) -> None:
        """One order changed (e.g. approved by the transformation server)."""
        invalidate = getattr(self._repo, "invalidate_order", None)
        if invalidate:
            invalidate(order_id)

    def place_restock(self, ctx: AccessContext, clinic_id: str, product: str,
                      quantity: int) -> OrderPlaced:
        """Doctor clinic restock: relationship-scoped WRITE. Authorization is
        doctor-only. The call center does NOT gate on approval itself — it submits
        the request to the transformation server, whose human-approval layer holds
        it for a person to approve. So this always SUBMITS and returns the pending
        acknowledgement; the caller is told it's awaiting approval."""
        ctx.require_verified()
        if ctx.role != "doctor":
            raise AccessDenied("clinic restock is for verified doctors only")
        request = OrderRequest(
            order_type="clinic_restocking", created_for="clinic",
            created_for_id=clinic_id, requested_by=ctx.subject_id,
            items=[OrderItem(product=product, quantity=quantity)],
            idempotency_key=str(uuid.uuid4()))
        return self._writer.place(request)
