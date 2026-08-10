"""
Real MCP server — ORDER domain (port 3003).

Tools:
  list_orders(call_id, verified, subject_id, role)            → caller's own orders
  lookup_order(call_id, verified, subject_id, role, order_id) → one own order

Identity arrives as parameters (the host vouches); the server REBUILDS the context
and RE-ENFORCES ownership — a caller can never read another patient's order, even
with a valid order id. Mirrors the patient server's trust-boundary pattern.

Run:
    ORDER_PORT=3003 python -m src.servers.order.run
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from src.servers.order.infrastructure.application import build_order_service
from src.shared.security import AccessContext, AccessDenied
from src.shared.logging_config import setup_logging
from src.shared.cache import get_cache, pending_key
from src.shared import database as db

setup_logging()          # this server's logs → the shared heartbug.log
mcp = FastMCP("heartbug-order")
log = logging.getLogger("heartbug")
_service = build_order_service()
_cache = get_cache()

# A caller may have ONE order awaiting approval at a time. The pending marker is
# keyed by OWNER — the patient for a self-order, the CLINIC for a restock (so a
# doctor can't slip a second one past by being a different person at the same
# clinic). Cleared by the transformation server's approval webhook; the TTL is the
# safety net so a lost webhook can never lock a caller out for good.
_PENDING_TTL = 24 * 3600


def _pending_for(owner_id: str) -> dict | None:
    """The owner's outstanding order, if any. Used by the PREVIEW tools for a fast,
    friendly early message ('you already have one pending'). It is NOT the guarantee
    — a check here then a write later is a race. The guarantee is _acquire_pending
    at submit time."""
    return _cache.get(pending_key(owner_id)) if owner_id else None


def _acquire_pending(owner_id: str, reference: str, product: str, quantity: int) -> bool:
    """Atomically claim the one-pending slot. True iff WE got it. Two concurrent
    submits race on SET NX and exactly one wins — this is the real lock, not the
    preview check. 24h TTL is the safety net so a lost approval webhook can't lock
    an owner out forever."""
    return _cache.set_if_absent(
        pending_key(owner_id),
        {"reference": reference, "product": product, "quantity": quantity},
        ttl_seconds=_PENDING_TTL)


def _update_pending(owner_id: str, reference: str, product: str, quantity: int) -> None:
    """Fill in the real reference once the order exists. We already hold the slot
    (acquired atomically at submit start), so a plain overwrite is safe — no race."""
    _cache.set(pending_key(owner_id),
               {"reference": reference, "product": product, "quantity": quantity},
               ttl_seconds=_PENDING_TTL)


def _blocked(pending: dict) -> dict:
    """One pending order per owner — tell them what's already in flight."""
    return {"found": False, "event": "order_already_pending",
            "reference": pending.get("reference"), "product": pending.get("product"),
            "quantity": pending.get("quantity")}


def _context(call_id: str, verified: bool, subject_id: str | None, role: str,
             clinic_id: str | None = None) -> AccessContext:
    ctx = AccessContext(call_id=call_id)
    if verified and subject_id:
        ctx.grant(subject_id, role=role, clinic_id=clinic_id)
    return ctx


def _as_dict(o) -> dict:
    return {"order_id": o.order_id, "product": o.product, "status": o.status,
            "created_at": o.created_at, "carrier": o.carrier,
            "tracking": o.tracking, "eta": o.eta}


@mcp.tool()
def list_orders(call_id: str, verified: bool, subject_id: str | None,
                role: str = "patient") -> dict:
    """List all the caller's orders with each order's delivery status. Use when
    the caller asks broadly about their orders without naming a specific one —
    e.g. "what are my orders", "where are my packages", "has anything shipped",
    "what have I ordered", "track my deliveries"."""
    ctx = _context(call_id, verified, subject_id, role)
    try:
        orders = _service.list_orders(ctx)
    except AccessDenied as e:
        return {"found": False, "reason": str(e)}
    return {"found": bool(orders), "count": len(orders),
            "orders": [_as_dict(o) for o in orders]}


@mcp.tool()
def lookup_order(call_id: str, verified: bool, subject_id: str | None,
                 order_id: str = "", role: str = "patient") -> dict:
    """Get the full status of ONE specific order named by its order number —
    its delivery status, carrier, tracking number, and expected delivery date.
    Use when the caller refers to a particular order, e.g. "where is order
    O1003", "status of my monitor order", "when will O1005 arrive",
    "track order number ...". Needs the order id.

    STATUS ONLY: this tool only READS an order's status. It does NOT change an
    order — it cannot update the delivery address, quantity, product, or cancel
    an order. If the caller asks to CHANGE or MODIFY an existing order (e.g. "ship
    it to a different address", "change the quantity", "cancel my order"), do NOT
    call this tool; the assistant should explain that orders can't be modified
    once placed and offer to connect them to support."""
    ctx = _context(call_id, verified, subject_id, role)
    try:
        order = _service.lookup_order(ctx, order_id)
    except AccessDenied as e:
        # denied and genuinely-missing narrate the SAME way on purpose: telling a
        # caller "that's not yours" would confirm the order exists.
        return {"found": False, "event": "order_not_found", "reason": str(e)}
    if order is None:
        return {"found": False, "event": "order_not_found",
                "reason": "order not found"}
    return {"found": True, **_as_dict(order)}


@mcp.tool()
def prewarm(call_id: str, verified: bool, subject_id: str | None,
            role: str = "patient") -> dict:
    """
    Warm the caller's 28-day order window into the server cache BEFORE they ask, so
    the first order request is a cache hit. The host calls this after verification
    (Sri's pre-load-while-in-queue strategy). Reads through the repository, which
    backfills the shared cache as a side effect.
    """
    ctx = _context(call_id, verified, subject_id, role)
    try:
        _service.list_orders(ctx)                  # populates orders:<id> cache
        return {"warmed": True, "subject_id": subject_id}
    except AccessDenied as e:
        return {"warmed": False, "reason": str(e)}


@mcp.tool()
def place_order(call_id: str, verified: bool, subject_id: str | None,
                role: str = "patient", product: str = "", quantity: int = 0) -> dict:
    """Place a new order for the CALLER'S OWN supplies (e.g. replacement stickers,
    leads, envelopes). Use when a patient wants to order or reorder something for
    themselves — "order more stickers", "I need new leads", "reorder my supplies".
    Needs both the product AND how many (quantity); do not assume a quantity.

    This PREVIEWS the order and reads back the delivery address for the caller to
    confirm — it does NOT submit anything. The submit happens only after the
    caller confirms (handled by the host).

    SELF-ONLY: the order is on the VERIFIED CALLER'S OWN account. Do NOT use it to
    order on behalf of someone else (a spouse, relative, or another patient) —
    supplies are tied to the account-holder's own monitoring."""
    if not product or quantity <= 0:
        return {"found": False, "event": "ask_quantity", "reason": "need product and quantity"}
    # one pending order per caller — check BEFORE previewing, so they hear it
    # before being asked to confirm rather than after saying yes
    pending = _pending_for(subject_id)
    if pending:
        return _blocked(pending)
    ship_to = db.patient_address(subject_id)          # caller's OWN registered address
    # preview only — the host stashes this and submits on a "yes"
    return {"found": True, "event": "order_confirm", "needs_confirmation": True,
            "product": product, "quantity": quantity, "address": ship_to}


@mcp.tool()
def submit_order(call_id: str, verified: bool, subject_id: str | None,
                 role: str = "patient", product: str = "", quantity: int = 0) -> dict:
    """[host-invoked only] Submit a previewed patient order after the caller has
    confirmed. Not selected by the planner — the host calls this on a 'yes'."""
    ctx = _context(call_id, verified, subject_id, role)
    ship_to = db.patient_address(subject_id)
    # claim the one-pending slot ATOMICALLY before doing the write. If we lose the
    # race, someone else already has an order in flight for this owner — block
    # without placing. Placeholder ref; filled in once the order exists.
    if not _acquire_pending(subject_id, "pending", product, quantity):
        return _blocked(_pending_for(subject_id) or {})
    try:
        placed = _service.place_order(ctx, product, quantity)
    except AccessDenied as e:
        _cache.delete(pending_key(subject_id))          # release on failure
        return {"found": False, "reason": str(e)}
    _update_pending(subject_id, placed.reference, product, quantity)
    _service.invalidate_owner_index("patient", subject_id)   # list must re-read
    return {"found": True, "event": "order_placed", "product": product,
            "quantity": quantity, "reference": placed.reference,
            "status": placed.status, "address": ship_to}


@mcp.tool()
def request_clinic_restock(call_id: str, verified: bool, subject_id: str | None,
                           role: str = "doctor", clinic_id: str = "",
                           product: str = "", quantity: int = 0) -> dict:
    """Request a CLINIC restock for a verified doctor (bulk supplies for the
    clinic, not for one patient). Use when a doctor asks to restock or reorder
    clinic stock — "order more stickers for the clinic", "restock our leads".
    Doctors ONLY — a patient can never restock a clinic.
    The clinic is taken from the doctor's verified identity — do NOT ask for it.
    Needs both the product AND how many; do not assume a quantity.

    This PREVIEWS the restock and reads back the clinic's shipping address for the
    doctor to confirm — it does NOT submit anything. The submit happens only after
    the doctor confirms (handled by the host)."""
    ctx = _context(call_id, verified, subject_id, role, clinic_id)
    if role != "doctor":
        return {"found": False, "event": "restock_denied",
                "reason": "clinic restock is for verified doctors only"}
    if not product or quantity <= 0:
        return {"found": False, "event": "ask_quantity", "reason": "need product and quantity"}
    # the clinic is the doctor's OWN verified clinic — never a caller-supplied value
    doctor_clinic = ctx.clinic_id
    if not doctor_clinic:
        return {"found": False, "event": "restock_no_clinic",
                "reason": "no clinic on the doctor's record"}
    # one pending restock per CLINIC — a colleague's outstanding order blocks too,
    # since the order belongs to the clinic, not the individual doctor
    pending = _pending_for(doctor_clinic)
    if pending:
        return _blocked(pending)
    clinic_label = db.clinic_name(doctor_clinic)
    ship_to = db.clinic_address(doctor_clinic)
    # preview only — the host stashes this and submits on a "yes"
    return {"found": True, "event": "restock_confirm", "needs_confirmation": True,
            "product": product, "quantity": quantity, "clinic": clinic_label,
            "address": ship_to}


@mcp.tool()
def submit_clinic_restock(call_id: str, verified: bool, subject_id: str | None,
                          role: str = "doctor", clinic_id: str = "",
                          product: str = "", quantity: int = 0) -> dict:
    """[host-invoked only] Submit a previewed clinic restock after the doctor has
    confirmed. Not selected by the planner — the host calls this on a 'yes'. The
    request is forwarded for human approval on the fulfilment side (pending)."""
    ctx = _context(call_id, verified, subject_id, role, clinic_id)
    doctor_clinic = ctx.clinic_id
    clinic_label = db.clinic_name(doctor_clinic)
    if not _acquire_pending(doctor_clinic, "pending", product, quantity):
        return _blocked(_pending_for(doctor_clinic) or {})
    try:
        placed = _service.place_restock(ctx, doctor_clinic, product, quantity)
    except AccessDenied as e:
        _cache.delete(pending_key(doctor_clinic))       # release on failure
        return {"found": False, "event": "restock_denied", "reason": str(e)}
    _update_pending(doctor_clinic, placed.reference, product, quantity)
    _service.invalidate_owner_index("clinic", doctor_clinic)
    return {"found": True, "event": "restock_submitted", "product": product,
            "quantity": quantity, "clinic": clinic_label,
            "reference": placed.reference, "status": placed.status}


@mcp.tool()
def order_decision(call_id: str = "", owner_id: str = "", order_id: str = "",
                   status: str = "", token: str = "") -> dict:
    """[transformation-server webhook] Record an approval decision for a submitted
    order and release the caller's pending slot so they can order again.

    Not part of the caller-facing flow — the fulfilment side calls this when a
    pending order is approved or rejected. Idempotent: replaying the same decision
    is harmless (the slot is simply already clear)."""
    expected = os.getenv("HEARTBUG_SERVICE_TOKEN", "internal-service-secret")
    if token != expected:
        return {"found": False, "reason": "unauthorized"}
    if not owner_id:
        return {"found": False, "reason": "owner_id required"}
    _cache.delete(pending_key(owner_id))            # release the one-pending slot
    if order_id:
        _service.invalidate_order(order_id)         # status changed → re-read it
    log.info(f"ORDER DECISION owner={owner_id} order={order_id} status={status}"
             f" → pending slot released")
    return {"found": True, "owner_id": owner_id, "order_id": order_id,
            "status": status, "released": True}


if __name__ == "__main__":
    port = int(os.getenv("ORDER_PORT", "3003"))
    mcp.settings.host = os.getenv("MCP_BIND_HOST", "127.0.0.1")
    # internal Docker network: services call each other by container name, which the
    # default DNS-rebinding guard (localhost-only) would reject with 421. Disable it
    # here; host validation belongs at the external gateway, not between services.
    from mcp.server.transport_security import TransportSecuritySettings
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=(os.getenv("MCP_DNS_REBIND_PROTECT", "0") == "1"))
    mcp.settings.port = port
    mcp.run(transport="streamable-http")