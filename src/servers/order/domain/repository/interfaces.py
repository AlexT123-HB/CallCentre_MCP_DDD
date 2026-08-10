"""Order repository interface — the domain's contract for order data access."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.servers.order.domain.models import Order, OrderPlaced, OrderRequest


class OrderRepository(ABC):
    @abstractmethod
    def get_by_id(self, order_id: str) -> Optional[Order]:
        ...

    @abstractmethod
    def list_for_patient(self, patient_id: str) -> List[Order]:
        ...
        
class OrderWriter(ABC):
    """The WRITE PORT — the seam the transformation server fills later. A stub
    implements it now; the real `POST /fulfilment/order` replaces the stub with no
    change above this interface. Both patient-order and clinic-restock use it.
    Implementations must be idempotent on request.idempotency_key (a dropped voice
    call must never create two orders)."""

    @abstractmethod
    def place(self, request: OrderRequest) -> OrderPlaced:
        ...
