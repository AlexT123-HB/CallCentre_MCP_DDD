"""
Order application layer — the anti-corruption layer (ACL).

Translates between the LLM's string world and the order domain's types, exposing
list_orders / lookup_order as tools. Thin: it delegates to the service, which holds
the ownership rules.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from src.servers.order.infrastructure.application import build_order_service
from src.shared.application_base import MCPApplicationService
from src.shared.security import AccessContext, AccessDenied


class OrderApplicationService(MCPApplicationService):
    def __init__(self, mcp=None) -> None:
        self._service = build_order_service()
        super().__init__(mcp=mcp)

    def list_orders(self, ctx: AccessContext) -> dict:
        try:
            orders = self._service.list_orders(ctx)
        except AccessDenied as e:
            return {"found": False, "reason": str(e)}
        return {"found": bool(orders), "count": len(orders),
                "orders": [self._as_dict(o) for o in orders]}

    def lookup_order(self, ctx: AccessContext, order_id: str) -> dict:
        try:
            order = self._service.lookup_order(ctx, order_id)
        except AccessDenied as e:
            return {"found": False, "reason": str(e)}
        if order is None:
            return {"found": False, "reason": "order not found"}
        return {"found": True, **self._as_dict(order)}

    @staticmethod
    def _as_dict(o) -> dict:
        return {"order_id": o.order_id, "product": o.product, "status": o.status,
                "created_at": o.created_at, "carrier": o.carrier,
                "tracking": o.tracking, "eta": o.eta}

    def tools(self) -> List[Tuple[str, str, Callable]]:
        return [
            ("list_orders",
             "List the verified caller's own orders.",
             self.list_orders),
            ("lookup_order",
             "Look up one of the caller's own orders by order id.",
             self.lookup_order),
        ]
