# HeartBug — Migrating the Host to LangGraph (design note)

> Status: **DECIDED IN PRINCIPLE, NOT YET BUILT.** Do this *after* the current
> host is stable and demoed — port a known-good flow, don't build the flow in a
> new framework. This note captures the full plan so we can pick it up later.

---

## TL;DR

- **Migrate the HOST only.** Keep MCP + DDD for the domains, untouched.
- LangGraph manages **across-turn** state: confirmations, escalation, slot-fill,
  resume-after-pause. That's where all our recent bugs lived.
- **Plan-and-select + the DAG executor stay OUR code**, inside one `execute` node.
  The plan is DATA in state, not graph topology.
- **All confirmations** (booking, reschedule, order, escalation) collapse to
  **one generic `resolve_confirm` node** driven by the manifest `confirmations`
  contract — same genericity we already have.
- The migration changes the *imperative `_turn` control flow* into an explicit
  graph. The distinctive parts (planner, DAG, domains, manifest contracts) change
  the LEAST.

---

## Why migrate at all

The recent host bugs were all **control-flow / state-ordering** problems, not
storage problems:
- confirmation "unclear" dead-end (popped pending state, fell through to nowhere)
- double-narrate (plan narrated, then escalation narrated again)
- stale `task_failure` counter re-firing handoffs
- "yes" classified as "no" (history poisoning the confirmation classifier)
- scattered `handoff.check()` calls (easy to forget one)

LangGraph structurally prevents most of these:
- `interrupt()` + checkpointer = explicit pause/resume → no "popped pending, fell
  through" dead-ends.
- One shared `escalation_check` node every path routes through → no scattered checks.
- One `resolve_confirm` node → no per-domain confirmation drift.

**Do NOT migrate if** the host stops accreting multi-turn/interrupt features — at
that point it's rewriting working code for structure alone. The trigger to migrate
is: interrupt points keep multiplying (we already have confirm, reschedule-confirm,
order-confirm, escalation-choice, slot-fill, and are adding clarification loops).

---

## Architecture: two levels, two engines

| Level | What | Engine |
|---|---|---|
| **Across-turn** | confirm, escalate, slot-fill, resume | **LangGraph** (`interrupt`, checkpointer, conditional edges) |
| **Within-turn** | plan → execute DAG → aggregate | **Our code** (plan_and_select + orchestrator), inside one node |
| **Domain calls** | verification, patient, order, doctor, kb, booking | **MCP + DDD** (untouched) |

Key insight: plan-and-select completes **within a single turn** — it's not
multi-turn state. So it stays our within-turn engine; LangGraph manages the
turn-to-turn state *around* it, not the DAG *inside* it.

---

## The graph (nodes + edges)

```
START
  │  graph.invoke(state, config={thread_id: call_id})
  ▼
entry  (conditional edge)                    ← checkpointer resumes here if paused
  ├── paused interrupt?  → resolve_confirm
  └── fresh turn?        → route_plan
        │
route_plan  (add_node)                        ← llm plan_and_select → state["subtasks"]
  ├── connect_human / small_talk / oos / from_context
  └── subtasks → execute
        │
execute  (add_node)                           ← OUR DAG executor runs state["subtasks"]
  │                                              via mcp_client; writes state["results"]
  │  a tool may return needs_confirmation=true
  ▼
set_pending + interrupt()  (add_node)         ← state["pending"] = manifest answers
  │                                              checkpointer saves, PAUSE turn
  ▼ (next turn resumes at →)
resolve_confirm  (ONE generic node)           ← classify reply vs state["pending"].answers
  ├── action:call_tool  → run declared submit_* tool
  ├── action:narrate    → cancelled/declined event
  ├── action:escalate   → F2 transfer
  └── unclear → tries<1 ? re_offer (interrupt again, tries+=1)
                        : force_transfer  Command(goto="escalate")
        │
escalation_check  (add_conditional_edges)     ← SHARED join: counters over threshold?
  ├── escalate → connect_now (offer)  interrupt()  → now/later/decline
  └── no       → narrate
        │
narrate  (add_node)                           ← llm say() → caller-facing reply
  ▼
END  (emit reply)   /   PAUSE (checkpoint) if an interrupt fired
```

### Node → LangGraph primitive

- `entry` — `add_conditional_edges(START, route_entry)`; returns `resolve_confirm`
  if checkpoint paused, else `route_plan`.
- `route_plan` — `add_node`; body = existing `plan_and_select`, writes `subtasks`.
- `execute` — `add_node`; body = existing orchestrator DAG dispatch over
  `state["subtasks"]` via `mcp_client`. (Or LangGraph `ToolNode`, but our own
  executor is fine and preserves the DAG + dependency logic.)
- `set_pending` — calls `interrupt(offer_text)`; stores the manifest answers map.
- `resolve_confirm` — `add_node`; body = existing `classify_confirmation`.
- unclear loop — conditional edge on `state["confirm_tries"]`; `re_offer` calls
  `interrupt()` again and increments; 2nd unclear → `Command(goto="escalate")`.
- `escalation_check` — `add_conditional_edges`; single shared join (replaces the
  ~5 scattered `handoff.check()` calls).
- `connect_now` — `interrupt(offer)` → resumes to `resolve_confirm` with the
  connect_now contract (now/later/decline).
- `narrate` — `add_node`; body = existing narrator.

---

## Confirmations: ONE generic node for ALL domains

We do NOT build a confirm node per domain. Every confirmable tool
(`request_callback→submit_callback`, `reschedule_callback→submit_reschedule`,
`place_order→submit_order`, `connect_now→now/later/decline`) returns
`needs_confirmation=true` with the SAME shape. The manifest `confirmations`
contract (answers map: label→{action, tool/event, hint}) rides into
`state["pending"]`. `resolve_confirm` reads that contract and dispatches — it
knows nothing domain-specific. This is exactly today's generic `_pending_confirm`
handler, expressed as one node.

Three action types → three edges out of `resolve_confirm`:
- `call_tool` → the declared submit tool (submit_callback / submit_reschedule /
  submit_order)
- `narrate`  → cancelled / declined event
- `escalate` → F2 transfer (connect_now "yes"/"now")

**Unclear handling (decided):** re-offer ONCE (`interrupt()` again, bump
`confirm_tries`), then on a 2nd unclear → **force transfer** (`Command(goto=
"escalate")`). Applies uniformly to every confirmation — booking, order,
escalation all get the same bounded recovery for free.

---

## Plan-and-select: stays OUR engine (the important bit)

LangGraph = one fixed graph topology. Our planner produces a **runtime-variable
DAG** (N subtasks, domains, `depends_on`). Do NOT try to express the DAG as graph
edges. Instead:

**Option A (chosen): subtasks are DATA in state, DAG runs inside `execute`.**
```python
class TurnState(TypedDict):
    subtasks: list[dict]      # the LLM plan — DATA
    results: dict[str, dict]  # keyed by subtask id
    ...
```
- `route_plan` writes `state["subtasks"]`
- `execute` loops the DAG internally (our orchestrator), writes `state["results"]`
- LangGraph sees ONE node; the DAG lives inside it.

→ `orchestrator.py` barely changes — it becomes the `execute` node body.

**Option B (Send API):** LangGraph `Send` does dynamic parallel fan-out
(`[Send("run_subtask", st) for st in subtasks]`). Good for PARALLEL subtasks
(“how is Alex AND Maria”), clumsy for DEPENDENT ones (`depends_on`). Only consider
if we want native fan-out; otherwise Option A keeps our working DAG intact.

**Option C (rebuild graph per turn):** rejected — fights the framework, loses
checkpointing.

### OPEN QUESTION to resolve before building
Does any subtask ever need to **pause mid-DAG for caller input** (a subtask hits a
confirmation partway through a multi-subtask plan)? 
- If NO (confirmations happen before execution or as the next turn) → Option A is
  clean, DAG stays fully inside one node.
- If YES → the interrupt must be at graph level but fires from inside the node —
  the clean boundary breaks. Would need to hoist that subtask's confirmation to a
  graph interrupt. Check the real flows before building.

---

## TurnState (sketch — the shared state object)

```python
class TurnState(TypedDict):
    # identity / context
    call_id: str
    ctx: dict                 # verified, role, subject_id (AccessContext)
    history: list             # conversation turns
    notes: dict               # structured working memory (entities + actions)
    # within-turn plan
    subtasks: list[dict]      # planner output
    results: dict[str, dict]  # per-subtask results
    # across-turn pending (interrupt state)
    pending: dict | None      # {domain, tool, args, answers, prompt}
    confirm_tries: int        # bounds the unclear re-offer loop (0 → 1 → force)
    # escalation counters
    verify_stuck: int
    task_failures: dict       # operation → consecutive fail count
    out_of_scope: int
    # output
    reply: str
```
Most of this is our current `session` dict — the mapping is close to 1:1. The
checkpointer persists this per `thread_id = call_id`.

---

## Checkpointer choice (IMPORTANT)

`interrupt()`/resume needs a **checkpointer keyed by thread_id** (= our call_id).
- Do NOT use in-memory `MemorySaver` for anything that must survive restart.
- We just moved bookings Redis→CSV *because Redis resets*. Same concern applies:
  use a **durable checkpointer** (Postgres, or Redis with real persistence) so a
  paused confirmation/escalation survives a restart mid-call.
- Per-call thread model fits perfectly: each call = one thread, each turn
  re-invokes the graph, checkpointer restores state, resumes at the paused node.

---

## What does NOT change (stays as-is)

- **MCP domain servers** — verification, patient, order, doctor, kb, booking.
  Called from `execute` and from the `call_tool` confirmation action.
- **DDD layers** inside each domain (models / service / repository / infra).
- **Manifest `confirmations` contracts** — they become the DATA driving
  `resolve_confirm`. Booking's and order's contracts are untouched.
- **submit_* tools** — host-invoked writes, still MCP tools.
- **classify_confirmation, plan_and_select, narrator, orchestrator DAG** — become
  node bodies, logic intact.
- **booking CSV store, scheduler, handoff sheet** — downstream of the domains,
  untouched.

## What DOES change

- The imperative `_turn` (nested `if` branches) → explicit graph nodes/edges.
- Hand-rolled `_pending_confirm` / `_pending` / `_pending_escalation_choice` →
  ONE `interrupt()` pattern + checkpointer.
- Scattered `handoff.check()` (~5 call sites) → ONE `escalation_check` join node.
- Confirmation "unclear" dead-end → bounded `re_offer`→`force_transfer` edges.

---

## Migration sequencing (recommended)

1. **NOW (pre-demo):** ship on the current imperative host. Fix the "unclear"
   dead-end with a small consistent pending-state helper (re-offer once → transfer)
   — low-risk, no framework.
2. **AFTER demo, IF interrupts keep growing:** port the now-stable host to
   LangGraph using this note. Port a known-good flow — the current behavior is the
   spec to test against, which makes the port safe.
3. Resolve the OPEN QUESTION (mid-DAG confirmation) against real flows first.
4. Pick a durable checkpointer up front.
5. Port node-by-node; keep the current host runnable as the reference oracle
   (diff outputs on the eval scenarios turn-by-turn).

---

## Risk notes

- Don't migrate mid-debugging — you'd debug the flow AND the framework at once.
- The distinctive IP (planner + DAG) changing least = LOW migration risk for the
  hard part; the risk is in faithfully re-expressing the turn control flow.
- Two mental models (MCP+DDD for domains, LangGraph for host) — acceptable, and
  the host is arguably clearer as a graph than as nested ifs.
- Keep the eval harness scenarios as the regression suite: same inputs must
  produce equivalent replies pre/post migration.
