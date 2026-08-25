"""
Telephony adapters — thin, swappable vendor front-ends over the one host core.

Each adapter translates a vendor's webhook/response format onto the host's
webhook()/turn(), reusing the shared decision logic in base.py. Vendors are peers:
adding one is a new <vendor>_adapter.py + a mount line in app.py; the core never
changes.

    twilio_adapter   -> /twilio/*    (TwiML)
    talkdesk_adapter -> /talkdesk/*  (JSON data-dip called by a Studio flow)

Import the submodules explicitly where needed (app.py mounts the routers); this
package __init__ stays import-light on purpose so touching `base` doesn't pull in
the adapters' web deps.
"""
