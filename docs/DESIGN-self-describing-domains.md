# Design Doc — Self-Describing Domains & LLM Orchestration

**Status:** IMPLEMENTED ✓ — manifests live; tool selection, slot-filling, and a
multi-domain planner all run on the self-describing foundation. Adding a domain
needs zero host edits.
**Author:** Alex
**Scope:** HeartBug MCP call-center host + domain architecture

---

## 1. Problem (original) — and what came after

The founding goal: **adding a new domain** (order, device, knowledge, doctor, …)
should be a *drop-in* — write the domain, register it, done. The 5th, 8th, 12th
domain should be as cheap to add as the 2nd.

Originally the host **hardcoded** knowledge about each domain in six separate
files, so adding one domain meant editing all six — a drift-and-bug surface we
had already been bitten by (a missing narration entry made the host speak the
wrong response for handoffs).

That original problem is **solved** (§2). Since then the system has grown three
capabilities on top of the self-describing foundation — **live tool selection**,
**generic slot-filling**, and a **multi-domain planner** — each of which stayed a
drop-in *because* the foundation was self-describing. This doc records the
implemented architecture end-to-end, and flags the one forward-looking item
(RAG-for-tools) deferred until scale demands it (§9).

---

## 2. The foundation — the domain manifest (IMPLEMENTED)

Each domain declares a **manifest**: one object describing everything the host
would otherwise hardcode. The host reads all registered manifests and configures
itself. Knowledge lives *with* the domain.

### 2.1 Manifest shape (current)

```
DomainManifest:
  name:                  "order"
  url:                   env("ORDER_URL", "http://localhost:3003/mcp")
  description:           "the caller's own HeartBug orders — status, tracking,
                          delivery; NOT product pricing or unrelated costs"
  requires_verification: true
  public:                false        # reachable in BOTH gate states (e.g. KB)
  primary_tool:          "list_orders"
  tools:                 ["list_orders", "lookup_order", "prewarm"]
  tool_events:           { list_orders: "order_list", lookup_order: "order_status" }
  required_args:         { lookup_order: { order_id: "ask_order_id" } }
  events:                { order_status: "...", order_list: "...", not_found: "..." }
  special_flow:          None          # "verification" only for the verify domain
  identity_source:       None          # set on caller-type domains (patient, doctor)
```

One object, living with the domain (`servers/<domain>/manifest.py`). Registered
in one line in `servers/manifests.py`.

### 2.2 How each field replaces a hardcoded host edit-point

| Manifest field | Replaced hardcoding | How the host uses it |
|----------------|--------------------|----------------------|
| `name` + `url` | server URL map | host builds the URL map from all manifests |
| `requires_verification` + `public` | gate rule | gate reads the flags (+ central policy, §4) |
| `description` | router prompt domain list | router/planner prompt is *generated* from manifests |
| `events` | narration event list | narration style is *assembled* from all manifests |
| `tools` + `tool_events` | tool map | tool selection reads the manifest's tools + their events |
| `required_args` | (new) slot-filling | generic slot-fill knows which args each tool needs |
| `identity_source` | (new) verification lookup | verification loops caller-type domains to find callers |
| (generic flow) | dispatch chain | one generic flow handles all domains; verification adds one step |

### 2.3 Adding a domain today

1. Write `servers/<domain>/` (the four-layer DDD structure) — addition
2. Write `servers/<domain>/run.py` (its FastMCP server) — addition
3. Declare `servers/<domain>/manifest.py` — addition
4. Register it — one line in `servers/manifests.py`

**Zero edits** to the host's routing, gate, narration, tool-selection, or planner.
Verified live: commenting out a domain's one registration line cleanly removes it
(gate stops offering it, router can't route to it) with no errors; uncommenting
restores it. Adding the KB and Order domains each required zero host edits.

---

## 3. What was built ON the foundation

The self-describing model turned each of these from a "touch many files" change
into a drop-in. All are implemented.

### 3.1 Live tool selection (via MCP discovery)

The host no longer maps "domain → one hardcoded tool." It **discovers** a domain's
tools at runtime over MCP (`list_tools`), cached per domain, and an LLM call picks
the right tool and extracts its args.

- **Discovery = capability** (what the server *can* do); **manifest `tools` =
  policy** (what's conversationally selectable). The candidate list is their
  intersection, so webhook-only tools (`lookup_by_phone`) and host-only tools
  (`prewarm`) are discovered but never offered to the LLM.
- **Single-tool domains skip the selection call** (patient, verification) — the
  one tool is called directly.
- `tool_events` maps the *selected* tool's result to its narration event, so a
  domain with several tools narrates each outcome correctly. A tool may also
  return its own `event` in the result (e.g. KB's answer / miss / needs-verify).

### 3.2 Generic slot-filling

Verification stopped being a special hardcoded flow. Name + DOB are simply the
**arguments** of the `verify_identity` tool, exactly like `order_id` is an argument
of `lookup_order`. A generic mechanism accumulates a tool's args across turns
(`SlotState`), and if a `required_args` slot is missing, the host asks for it
(using the slot-prompt event the manifest declares).

- Verification's **only** remaining special step is `grant()` on success —
  promoting the security context. Everything else (select tool, extract args,
  slot-fill) is the generic path shared with all domains.
- New slot-filling domains (e.g. a future booking flow) are drop-ins: declare the
  tool's `required_args` and the machinery handles the back-and-forth.

### 3.3 Multi-domain planner + DAG executor

One caller request can span domains ("my info and my orders"). A **planner** LLM
call decomposes the request into subtasks — deciding at runtime, from the
discovered tool descriptions, which domains AND whether any subtask depends on
another (`depends_on`). A **DAG executor** runs independent subtasks concurrently
and dependent ones in order, feeding a prior step's result into the next step's
tool selection, then narrates one combined reply.

- **Parallel and sequential** use the same executor loop — parallel is just the
  case where all steps are ready at once; dependencies unlock steps round by round.
- The planner only proposes; the plan is validated against the gate (disallowed
  domains dropped, bad references dropped) before execution.
- **Nothing enumerates dependency cases** — the LLM infers them per call from the
  request + tool descriptions, so a new domain chains for free.
- Two orchestration modes exist for evaluation (`HEARTBUG_ORCH`): **A** =
  plan → per-domain tool-selection (separate calls, lower hallucination risk on
  many tools); **B** = one merged plan-and-select call (fewer calls). Kept
  side-by-side to measure the accuracy/latency trade-off.

This follows the **Planner + DAG (V3)** pattern: one planning call upfront,
mechanical concurrent execution — predictable call count (not the 17-34 variance
of a free runtime orchestrator), and an inspectable, auditable plan, which suits a
latency-bound, compliance-sensitive voice system.

---

## 4. Security guardrail (non-negotiable for healthcare)

Two manifest fields govern reachability and are **security-critical**:
`requires_verification` and `public`.

If a domain could self-declare itself reachable by **unverified** callers and the
host trusted it blindly, adding a domain could open patient-adjacent data to
strangers — unacceptable.

**Rule: mechanical fields are trusted from the manifest; reachability is validated
against a central policy.**

- Mechanical (trusted): `url`, `description`, `events`, `tools`, `tool_events`,
  `required_args`, `identity_source`.
- Reachability (validated centrally): a domain is honored as pre-verification
  reachable **only if** its manifest declares it open **and** it appears on the
  central `PRE_VERIFICATION_ALLOWLIST` (today `{verification, kb}`).

Consequences:

- Adding a **normal verified domain** → pure drop-in, no security review.
- Adding a **pre-verification-reachable domain** (like KB) → requires a deliberate
  one-line addition to the central allowlist — a conscious, auditable decision.
- The `public` flag lets a domain (KB) be reachable in **both** states — general
  questions before verifying, with per-entry sensitivity gating so that
  patient-specific KB entries still require verification.

**Principle: extensibility for the plumbing; explicit central control for
security.** The allowlist is intentionally central and hardcoded — it is the
security review surface, not a smell.

---

## 5. Out-of-scope & knowledge-base interaction

Adding the KB domain sharpened routing. General product questions ("can I record
with the screen off?") now route to KB and are answered from a curated knowledge
base (pre-written answers + local embedding retrieval, alias-aware). This means:

- **Out-of-scope is now a clean, rare outcome.** A request that matches no domain
  (including KB) — e.g. "what's the price of ten kilos of mango?" — is declined
  plainly ("I only support HeartBug requests"). It is **not** steered into
  verification, and it never reaches tool selection.
- **`unknown` is terminal.** When the router returns no domain, the turn ends at
  the out-of-scope narration — no tool discovery, no slot-filling — in every path
  and mode.

---

## 6. Current domains

| Domain | Role | Verification | Notes |
|--------|------|-------------|-------|
| verification | verify caller by name + DOB | pre-verify (gate) | slot-filling + `grant()` |
| patient | caller's own record | required | single-tool |
| order | caller's own orders | required | multi-tool (list / lookup) |
| doctor | doctor as a caller type | required | `identity_source`; care-relationship access |
| kb | general product Q&A | public (both states) | retrieval + per-entry sensitivity gate |

Handoff (escalation) and out-of-scope handling remain **host-level services**, not
domains — a deliberate choice: they are cross-cutting outcomes reachable from any
flow, not destinations the router routes to.

---

## 7. What this is NOT (scope boundaries)

- **Not** a change to domain internals (models, services, repositories) — those
  stay four-layer DDD, dependencies pointing inward.
- **Not** host↔domain contract enforcement (structured output schemas so a domain
  can't silently break the host) — a separate hardening step (§10).
- **Not** RAG-for-tools — deferred until the tool count makes it worthwhile (§9).

---

## 8. Trade-offs (honest)

**Pros**
- Adding a domain is a true drop-in (folder + manifest + one line).
- Tool selection, slot-filling, and multi-domain orchestration all inherited the
  drop-in property — new domains get them for free.
- Domain knowledge lives *with* the domain; the host stays generic.
- Leans into MCP's native discovery rather than hardcoding what the protocol
  reports dynamically.
- Predictable, auditable orchestration (Planner + DAG) fits voice latency and
  healthcare compliance.

**Cons / costs**
- Indirection: the host reads manifests + discovers tools rather than using inline
  constants — a small readability cost for a large extensibility gain.
- The multi-domain planner adds LLM calls; mode A/B exists precisely to measure
  whether the merged call trades accuracy for latency.
- Requires discipline: the security guardrail (§4) must be respected, and tool
  descriptions must be written well (they drive selection now and retrieval later,
  §9).

---

## 9. Forward-looking — RAG for tools (DEFERRED, by design)

Today the gate + planner already narrow the tool set to the allowed domains'
tools (~a handful), so the LLM's tool context is small and selection is reliable.
As domains grow (say 15+), putting every allowed domain's tools into the
plan/select call would bloat the context and risk "lost in the middle" attention
degradation.

**Plan:** when the tool count grows, add **semantic tool retrieval** — embed each
tool's description + args, and at query time retrieve only the top-k relevant
tools to feed the LLM. This is "selective injection" / RAG-for-tools: the tool
descriptions become retrievable context rather than a fixed dump.

**Why it's cheap to defer:** the change is localized to the one function that
assembles the candidate tool list (`_candidate_tools`) — a swap of "return all
allowed-domain tools" for "retrieve top-k." Everything downstream (selection,
dispatch, narration) is unaffected, and the embedding infrastructure already
exists (used by the KB). Estimated a few hours of wiring + tuning when needed —
not a rewrite.

**The one discipline to keep now:** write **rich, distinct tool descriptions**
(purpose + the vocabulary callers actually use, mirroring the KB's aliases). Good
descriptions improve today's selection accuracy *and* make future retrieval work
well — the only part that is painful to retrofit across many domains later.

**Near-term alternative if context pressure appears before 15 domains:** OpenAI
prompt caching (automatic, GPT-5-family) caches a stable tool/description prefix at
up to ~90% input-token discount, and `allowed_tools` narrows per-turn selection
without changing (and thus busting) the cached prefix. Lower-risk than retrieval
because the full tool list stays available — no top-k miss.

---

## 10. Complementary hardening (separate efforts)

1. **Contract enforcement** — structured output schemas on every tool, validated
   at the host↔domain boundary, so a domain change cannot silently break the host.
   (Manifests make domains self-*describing*; schemas make the contract
   self-*enforcing*.)
2. **Operational stability** — per-domain tests, per-call timeouts, circuit
   breakers, distributed tracing across domain servers (the "chain" view).
3. **Real infrastructure** — Postgres (with connection pooling) and Redis behind
   the existing repository/cache interfaces; host↔server authentication.

---

## 11. Status summary

| Item | Status |
|------|--------|
| Self-describing manifests (zero-edit domain adds) | ✅ implemented |
| Central security allowlist for pre-verification reachability | ✅ implemented |
| Live MCP tool discovery + LLM tool selection | ✅ implemented |
| Generic slot-filling (verification unified into the generic flow) | ✅ implemented |
| Multi-domain planner + DAG executor (parallel + sequential) | ✅ implemented |
| KB domain (public, per-entry sensitivity) | ✅ implemented |
| Orchestration mode A/B for evaluation | ✅ implemented |
| RAG-for-tools (semantic tool retrieval) | ⏳ deferred to ~15+ tools (§9) |
| Contract schemas, operational stability, real infra | ⏳ separate efforts (§10) |
