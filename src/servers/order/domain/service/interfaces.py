"""Order service interface — the domain's order operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.servers.order.domain.models import Order, OrderPlaced
from src.shared.security import AccessContext


class OrderService(ABC):
    @abstractmethod
    def list_orders(self, ctx: AccessContext) -> List[Order]:
        ...

    @abstractmethod
    def lookup_order(self, ctx: AccessContext, order_id: str) -> Optional[Order]:
        ...
    
    @abstractmethod
    def place_order(self, ctx: AccessContext, product: str, quantity: int) -> OrderPlaced:
        """A patient places an order for THEMSELVES (self-ownership)."""
        ...

    @abstractmethod
    def place_restock(self, ctx: AccessContext, clinic_id: str, product: str,
                      quantity: int, approved: bool = False) -> OrderPlaced:
        """A doctor places a CLINIC restock order (relationship-scoped, write-gated)."""
        ...
    
