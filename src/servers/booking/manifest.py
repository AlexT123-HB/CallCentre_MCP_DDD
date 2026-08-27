"""Booking domain — self-description for the host."""

import os

from src.shared.manifest import DomainManifest

MANIFEST = DomainManifest(
    name="booking",
    url=os.getenv("BOOKING_URL", "http://localhost:3006/mcp"),
    description="bookings, appointments, and scheduling for the verified caller's own callback",
    requires_verification=True,         # verified-only — private to the caller
    primary_tool="request_callback",
    tools=["request_callback", "submit_callback", "reschedule_callback", "submit_reschedule", "cancel_callback", "get_my_callback"],
    tool_events={"request_callback": "callback_confirm", "submit_callback": "callback_recorded",
                 "reschedule_callback": "reschedule_confirm", "submit_reschedule": "reschedule_recorded",
                 "cancel_callback": "cancel_confirmed", "get_my_callback": "callback_details"},
    required_args={
        "request_callback": {"date": "ask_date", "time": "ask_time"},
        "submit_callback": {"date": "ask_date", "time": "ask_time"},
    },
    confirmations={
        # preview tool → how to resolve the caller's confirmation reply. The host
        # is generic: it classifies the reply into one of these labels (using the
        # hints) and runs the declared action. Submit tools are host-invoked here,
        # never planner-selectable — the LLM can't trigger a write.
        "request_callback": {
            "answers": {
                "yes": {"action": "call_tool", "tool": "submit_callback",
                        "hint": "the caller confirms the callback time is right"},
                "no":  {"action": "narrate", "event": "callback_cancelled",
                        "hint": "the caller declines / wants a different time"},
            }
        },
        "reschedule_callback": {
            "answers": {
                "yes": {"action": "call_tool", "tool": "submit_reschedule",
                        "hint": "the caller confirms the new callback time"},
                "no":  {"action": "narrate", "event": "reschedule_cancelled",
                        "hint": "the caller declines the new time / keeps the old one"},
            }
        },
    },
    special_flow=None,                  # generic domain flow
    events={
        "ask_date": "ask which day they'd like the callback",
        "ask_time": "ask what time of day they'd like the callback",
        "callback_need_date": "ask which day they'd like the callback",
        "callback_need_time": "ask what time they'd like the callback",
        "callback_out_of_hours": "explain the team is only available on weekdays during "
                                 "working hours (open_hour to close_hour) and the time they "
                                 "gave isn't available; ask them to pick a weekday time in "
                                 "those hours",
        "callback_confirm": "read the scheduled date and time back and ask them to confirm "
                            "it's right. Tell them the call will come AROUND that time but "
                            "may vary by up to window_min minutes. Wait for yes/no — do not "
                            "say it's booked yet.",
        "callback_recorded": "confirm the callback is now booked (read the time back), give "
                             "them the booking reference (booking_id) they can quote, and say "
                             "a staff member will call around then, within about window_min "
                             "minutes.",
        "callback_cancelled": "acknowledge you haven't booked it, and ask if they'd like a "
                              "different day or time",
        "reschedule_confirm": "read back the NEW date and time and ask them to confirm the "
                              "move from their old time. Wait for yes/no — don't say it's "
                              "changed yet.",
        "reschedule_recorded": "confirm the callback has been moved to the new time (read it "
                           "back) and that a staff member will call around then, within about "
                           "window_min minutes.",
        "reschedule_cancelled": "confirm you've kept their original callback time unchanged",
        "callback_none_to_change": "tell them you don't have a callback booked for them, and "
                                   "offer to book one",
        "cancel_confirmed": "confirm their callback has been cancelled",
        "callback_details": "tell them when their callback is booked (read back the date, "
                            "time and booking reference) and that staff will call within "
                            "about window_min minutes of it",
        "callback_none": "tell them they have no callback currently booked, and offer to "
                         "book one",
    },
)