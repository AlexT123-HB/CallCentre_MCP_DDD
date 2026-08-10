"""Order adaptor — reads the shared ORDERS table, maps rows to Order models."""

from __future__ import annotations

from typing import List, Optional

from src.servers.order.domain.models import Order
from src.shared import database as db


def _to_order(row: dict) -> Order:
    return Order(
        order_id=row["order_id"], patient_id=row["patient_id"],
        product=row["product"], status=row["status"], created_at=row["created_at"],
        carrier=row.get("carrier"), tracking=row.get("tracking"), eta=row.get("eta"),
    )


class OrderDataAdaptor:
    def fetch_by_id(self, order_id: str) -> Optional[Order]:
        row = db.ORDERS.get(order_id)
        return _to_order(row) if row else None

    def fetch_for_patient(self, patient_id: str) -> List[Order]:
        order_ids = db.ORDERS_BY_PATIENT.get(patient_id, [])
        return [_to_order(db.ORDERS[oid]) for oid in order_ids]
