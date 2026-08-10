"""Order infrastructure — DI wiring (factory)."""

from __future__ import annotations

from src.servers.order.domain.service.services import DefaultOrderService
from src.servers.order.infrastructure.repositories import OrderRepositoryImpl
from src.servers.order.infrastructure.order_writer_stub import StubOrderWriter


def build_order_service() -> DefaultOrderService:
    # StubOrderWriter is the write-port stand-in; swap for the transformation
    # server's writer later with no change to the service or anything above it.
    return DefaultOrderService(OrderRepositoryImpl(), StubOrderWriter())
