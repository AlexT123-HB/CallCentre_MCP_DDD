"""Order domain — self-description for the host."""

import os

from src.shared.manifest import DomainManifest

MANIFEST = DomainManifest(
    name="order",
    url=os.getenv("ORDER_URL", "http://localhost:3003/mcp"),
    description="orders, shipments, delivery status, tracking, and order history",
    requires_verification=True,         # verified-only — private to the caller
    primary_tool="list_orders",
    tools=["list_orders", "lookup_order", "place_order", "request_clinic_restock", "prewarm"],
    tool_events={"list_orders": "order_list", "lookup_order": "order_status",
                 "place_order": "order_confirm", "request_clinic_restock": "restock_confirm"},
    required_args={
        "lookup_order": {"order_id": "ask_order_id"},
        "place_order": {"product": "ask_product", "quantity": "ask_quantity"},
        "request_clinic_restock": {"product": "ask_product", "quantity": "ask_quantity"},
    },
    confirmations={
        # preview tool → how to resolve the caller's confirmation reply. The host
        # is generic: it classifies the reply into one of these labels (using the
        # hints) and runs the declared action. Submit tools are host-invoked here,
        # never planner-selectable — the LLM can't trigger a write.
        "place_order": {
            "answers": {
                "yes":            {"action": "call_tool", "tool": "submit_order",
                                   "hint": "the caller confirms / agrees / wants to proceed"},
                "no":             {"action": "narrate",   "event": "order_cancelled",
                                   "hint": "the caller declines / cancels the order"},
                "change_address": {"action": "escalate",  "event": "address_change_escalate",
                                   "hint": "the caller wants it shipped somewhere other than the address on file"},
            }
        },
        "request_clinic_restock": {
            "answers": {
                "yes":            {"action": "call_tool", "tool": "submit_clinic_restock",
                                   "hint": "the caller confirms / agrees / wants to submit it"},
                "no":             {"action": "narrate",   "event": "restock_cancelled",
                                   "hint": "the caller declines / cancels the restock"},
                "change_address": {"action": "escalate",  "event": "address_change_escalate",
                                   "hint": "the caller wants it shipped somewhere other than the clinic's address on file"},
            }
        },
    },
    special_flow=None,                  # generic domain flow
    events={
        "order_status": "tell them the status, carrier, tracking and delivery date "
                        "of the order from the facts",
        "order_list":   "summarize the caller's recent orders (product + status) "
                        "from the facts, briefly",
        "order_placed": "confirm the order has been submitted for processing, read back "
                        "the product, quantity, and the reference number from the facts, "
                        "and say they'll get a confirmation",
        "order_confirm": "read back the product and quantity, and tell the caller you'll "
                         "ship it to their address on file (the address in the facts) — ask "
                         "them to confirm that's correct before you place it",
        "address_change_escalate": "explain that, for security, orders can only be shipped to "
                                   "the address on file, so you'll connect them to the team to "
                                   "arrange delivery to a different address",
        "restock_submitted": "tell the doctor their clinic restock request (product, quantity, "
                             "clinic name from the facts) has been submitted and is now pending "
                             "human approval on the fulfilment side; give the reference number and "
                             "say they'll be notified once it's approved",
        "restock_confirm": "you're about to submit a clinic restock (product, quantity, clinic name "
                           "from the facts) for approval, shipping to the clinic's address (the "
                           "address in the facts) — read it back and ask the doctor to confirm "
                           "they want you to submit it",
        "restock_cancelled": "acknowledge that you've cancelled the clinic restock request and "
                             "ask if there's anything else",
        "order_cancelled": "acknowledge that you've cancelled the order and ask if there's "
                           "anything else",
        "order_already_pending": "explain they already have an order awaiting approval "
                                 "(give the product, quantity and reference from the facts) "
                                 "and that a new one can't be submitted until that one is "
                                 "approved — offer to help with anything else",
        "restock_denied": "explain that clinic restock requests can only be submitted by a "
                          "verified doctor, so you can't submit this one",
        "order_not_found":    "could not find that order, ask them to double-check the "
                        "order number",
        "restock_no_clinic": "explain that there's no clinic on their record, so you can't "
                             "process a clinic restock, and you'll connect them to the team "
                             "to sort it out",
        "ask_order_id": "ask for the order number",
        "ask_product":  "ask which product they'd like to order",
        "ask_quantity": "ask how many they'd like to order",
    },
)