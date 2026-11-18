# HeartBug — Architecture (start here)

> New to this codebase? Read this first. It's the map: what the system is, where
> things live, why it's built this way, and how to make your first change. ~20 min.

---

## What it is, in one paragraph

HeartBug Contact Centre is an agentic AI support assistant for a cardiac-monitoring service. A
caller (patient in the Bugsy app, or a doctor on the clinical phone line) sends a
message; the system verifies who they are, figures out what they want, calls the
right backend to do it, and speaks a natural reply. It's built on **MCP (Model
Context Protocol) + Domain-Driven Design**: a central **host** orchestrates the
conversation, and each business capability is an independent **domain server**
(verification, patient, order, doctor, kb, booking). The host never contains
business logic; the domains never contain conversation logic.

---

## The two halves

```
┌─────────────── HOST (the conversation brain) ────────────────┐
│  src/client/                                                 │
│  route → plan → (slot-fill) → execute → (confirm) → narrate  │
│  + verification gate, escalation, faithfulness, sessions     │
└──────────────────────────────┬───────────────────────────────┘
                               │ calls tools via the Orchestrator
                               ▼
┌─────────────── DOMAINS (the business capabilities) ───────────┐
│  src/servers/<domain>/   — each an independent MCP server     │
│  verification · patient · order · doctor · kb · booking       │
│  each: manifest (what it offers) + run.py (tools) + DDD layers│
└───────────────────────────────────────────────────────────────┘
```

**The host decides WHAT to do; the domains DO it.** That split is the whole design.

---

## Where does X live?

| I want to change... | Look in |
|---|---|
| How a caller message is routed to a domain | `src/client/router.py`, `src/client/providers/openai_provider.py` (the LLM planner) |
| The turn flow (route→plan→execute→narrate) | `src/client/host.py` (`_turn`) |
| What tools a domain offers + its narration events | `src/servers/<domain>/manifest.py` |
| A domain's actual tool logic | `src/servers/<domain>/run.py` (thin) → `domain/service/services.py` |
| A domain's data model / business rules | `src/servers/<domain>/domain/models.py` |
| Where a domain stores data | `src/servers/<domain>/infrastructure/repositories.py` |
| Access control (who can reach what) | `src/client/gate.py` (verification/role) + per-tool `if role !=` checks |
| Escalation to a human (now/later/decline) | `src/client/host.py` (the `_pending_escalation` handler + `_handoff_response`) |
| The 3-strikes counters | `src/client/handoff.py` |
| Slot-filling (asking for missing args) | `src/client/slots.py` + `host._run_plan` |
| Confirmation-before-write | `manifest.py` `confirmations` + `host` confirm handler |
| How the reply is worded | `src/client/narration.py` + `providers/openai_provider.py` (`narrate`) |
| Session state (per call) | `src/client/session.py`, stored in Redis |
| Working memory / call notes | `src/client/notes.py` |
| A/B orchestration mode + faithfulness | `src/client/mode_router.py`, `src/client/faithfulness.py` |
| Callback scheduling | `src/servers/booking` |
| Telephony vendor adapters | `src/client/telephony/` (twilio, talkdesk) |
| Outbound calls (LiveKit+Twilio) | `src/agents/outbound` |

---

## One turn, end to end

A caller message (`POST /turn {call_id, transcript}`) flows through `host._turn`:

1. **Load session** — ctx (verified? role?), history, counters, notes (`session.py`).
2. **Waiting on a reply?** — if a confirmation / escalation choice / slot is pending
   from last turn, resolve it first (don't re-route the reply).
3. **Route + plan** — the LLM picks domain(s) + tool(s) + args (`openai_provider.py`),
   constrained by the **gate** (`gate.py` — only tools allowed for this verify-state
   and role).
4. **Slot-fill** — if a chosen tool is missing a required arg, ask for it and pause
   (resumes next turn).
5. **Execute** — the `Orchestrator` (`orchestrator.py`) calls the domain server via
   `mcp_client.py`, wrapping the call in an `AccessContext` (identity/role — this is
   where security is enforced).
6. **Confirm** — if the tool returned `needs_confirmation`, read it back and wait for
   yes/no (a write only happens after "yes").
7. **Escalate** — if counters tripped (or the caller asked for a human), offer
   now/later/decline (`_handoff_response`).
8. **Narrate** — turn the result into a natural reply (`narration.py`).
9. **Save + respond** — persist the session, return the reply.

Steps 2, 4, 6, 7 are the *pause points* — where a turn can end waiting for the
caller and pick up next turn.

---

## The core principles 

Understanding these five explains most "why is it done like that?" questions:

1. **Gate, not prompt.** Access control (verification, roles, per-caller scoping) is
   enforced in **code** (`if role != "doctor"`, the gate), never asked of the LLM in
   a prompt. A prompt injection can override a sentence; it can't override an `if`.

2. **Declare, don't wire.** A domain describes itself in its `manifest.py` (its tools,
   slot requirements, confirmations, narration events). The host is generic, it reads
   the manifest. Adding a tool needs a manifest entry, not host changes.

3. **Escalation is orchestration, not a domain.** Connecting a human is the host
   detecting a condition and routingm, it has no lifecycle/data of its own, so it
   lives in the host, not in a domain.

4. **Instructions are authoritative over history.** The system's event/tool/
   confirmation instructions decide what happens each turn; conversation history
   informs but never overrides. (Getting this wrong caused a whole class of bugs.)

---

## The domain layout 

```
src/servers/<domain>/
├── manifest.py                  ← what the domain offers (tools, events, confirmations)
├── run.py                       ← the MCP tools (THIN — delegate to the service)
├── domain/
│   ├── models.py                ← entities + business rules (pure, no I/O)
│   ├── service/services.py      ← the business logic
│   ├── service/interfaces.py    ← the service port
│   └── repository/interfaces.py ← the storage port
└── infrastructure/
    ├── application.py           ← wires service + repository (composition root)
    └── repositories.py          ← the concrete store (cache / CSV / DB)
```

`booking` and `order` are the clearest full examples. `verification`/`kb` are
simpler. To understand any domain, read its `manifest.py` first (what it does), then
`run.py` (the tool surface), then `service/services.py` (the logic).

---

## Add a tool (the declare-don't-wire path)

Say you want a `get_clinic_hours` tool in the `clinic` domain:

1. **Implement it** in `src/servers/clinic/run.py`, a thin function that calls the
   service.
2. **Add the logic** in `domain/service/services.py`.
3. **Declare it** in `src/servers/clinic/manifest.py`: add to `tools`, map a narration
   event in `tool_events`, add the event text to `events`, and (if it needs args) add
   to `required_args`.
4. **Done.** The host picks it up automatically, no host code changes. The planner
   can now select it, slot-fill its args, and narrate its result.

That's the whole pattern. Don't change anything in `host.py` to add a domain
tool or logic.

---

## What to read, in order

1. This file.
2. `src/servers/booking/` — a complete, modern domain (read manifest → run → service).
3. `src/client/host.py` `_turn` — the conversation pipeline.
4. `docs/ORCHESTRATION-RULES.md` — the rules/enforcement layers.
5. The design notes (`LANGGRAPH_HOST_MIGRATION.md`, `LIVEKIT_ESCALATION_PLAN.md`) —
   where the system is heading.

---

## Things that will confuse you (and why they're intentional)

- **Two role-gate mechanisms**: domain-level (`gate.py`) for whole-domain access, and
  tool-level (`if role !=` in run.py) for one tool in a mixed-role domain. Both are
  code gates; they operate at different granularities.
- **`submit_*` tools aren't in the manifest `tools` list**: they're host-invoked only
  (after a confirmation "yes"), so the planner can't call them directly. This is
  deliberate: it makes "no write without confirmation" a guarantee, not a hope.
- **The booking store is a CSV**: chosen over Redis for durability (bookings must
  survive restarts + be staff-readable). Same repository port; the store is swappable.
- **Deployed code can lag the canonical zip**: the #1 source of "the fix doesn't
  work." Always confirm the running code matches the repo before debugging.
