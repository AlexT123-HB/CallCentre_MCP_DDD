"""Order domain models — pure business objects, no external dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class Order:
    """An order from HeartBug of patient. Identity is order_id."""
    order_id: str
    patient_id: str
    product: str
    status: str
    created_at: str
    carrier: Optional[str] = None
    tracking: Optional[str] = None
    eta: Optional[str] = None

    def belongs_to(self, subject_id: str) -> bool:
        """Ownership: is this order the caller's own?"""
        return self.patient_id == subject_id

# ── order PLACEMENT (writes) ─────────────────────────────────────────────────
# Shaped to match the real fulfilment API (orderType, orderItems, createdFor) so
# the write-port swap to the transformation server is a field-mapping, not a
# redesign. Two order types share one shape: patient self-order and clinic restock.

@dataclass
class OrderItem:
    product: str               # "sticker", "lead", "envelope", …
    quantity: int


@dataclass
class OrderRequest:
    """A request to PLACE an order. `created_for`/`created_for_id` distinguish a
    patient self-order (for="patient") from a clinic restock (for="clinic")."""
    order_type: str            # "patient_order" | "clinic_restocking"
    created_for: str           # "patient" | "clinic"
    created_for_id: str        # patient_id or clinic_id
    items: List[OrderItem] = field(default_factory=list)
    requested_by: str = ""     # subject_id of the caller who placed it
    idempotency_key: str = ""  # carried now (stub ignores) so retries can't double-order, remove after swap to fulfilment server


@dataclass
class OrderPlaced:
    """Outcome of placing an order. `status` mirrors the real API's lifecycle
    (pending until fulfilment confirms) so narration stays true after the swap."""
    accepted: bool
    reference: str = ""        # order id / reference to read back to the caller
    status: str = "pending"
    reason: str = ""
    needs_human: bool = False

