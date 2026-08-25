# HeartBug MCP Call Center

An automated inbound phone-support system for a cardiac-monitoring company, built
as an **LLM host** orchestrating a set of **self-describing domain servers** over
MCP (Model Context Protocol), following Domain-Driven Design.

A caller phones in → the **host** verifies them, decides which domain(s) handle the
request, calls the matching MCP tools, and speaks one reply. Each **domain**
(patient, order, doctor, knowledge base, verification) is an independent MCP server
that describes itself via a *manifest*, so the host configures itself — adding a
domain is a drop-in, no host edits.

The design is built around **structural guarantees, not prompt instructions**: the
LLM can never set a caller's identity (it's injected from the verified session and
re-checked at each domain server), can never place an order directly (writes are a
preview/submit split where the submit tool isn't in the model's tool list), and can
never reach another domain's data in Redis (per-domain ACL users enforce it). Full
MCP request tracing (MLflow) makes every one of these decisions inspectable.

---

## Top-level layout

```
src/
├── client/      # the HOST — the brain that runs the call
├── servers/     # the DOMAINS — independent MCP servers, one per capability
└── shared/      # cross-cutting pieces used by both (manifest, cache, security, db)
```

- **`client/`** = the orchestrator the caller talks to. It holds no business data;
  it routes, plans, calls tools, and narrates.
- **`servers/`** = the capabilities. Each is a four-layer DDD service exposed as an
  MCP server the host reaches over the wire.
- **`shared/`** = the contracts and infrastructure both sides rely on.

---

## `src/client/` — the host (orchestration brain)

The host receives each turn, decides what to do, and produces the spoken reply.

| File | What it does |
|------|--------------|
| `app.py` | The FastAPI entry point. Exposes `POST /webhook` (call start, provisional phone match + greeting) and `POST /{domain}` (one conversational turn). Thin — delegates to `host.py`. |
| `host.py` | **The core.** Runs each turn: gate → plan → slot-fill → execute → narrate. Contains the DAG executor (`_execute_dag`), single-domain execution (`_execute_single`), slot-filling across turns, small-talk / answer-from-context routing, confirmation handling, and both orchestration modes (A and B). Loads the session once at turn start and saves once at the end, so the ~19 in-turn mutations need no per-site persistence. |
| `notes.py` | **Call working memory.** After each successful tool call, distils the result into a compact structured note (entities + actions) written *in code* from tool results — no extra LLM call, ids copied verbatim. Injected on every turn as a block the history window can't evict, so a follow-up ("when will *it* arrive?") resolves against real ids even late in a long call. Holds only caller-facing identifiers (order numbers, names), never internal DB keys. |
| `gate.py` | Decides which domains a caller may reach **right now**, based on verification state + the manifest's `requires_verification`/`public` flags + the central security allowlist. Produces the candidate set the router/planner chooses from. |
| `router.py` | Wraps the LLM "classify this turn → which domain" call. Used for the unverified path and for redirect detection. |
| `orchestrator.py` | Fans out `DomainCall`s to the domain servers (concurrently) and collects results/failures. The mechanical "call the tools" step. |
| `mcp_client.py` | The MCP wire client. Connects to each domain server by URL, discovers its tools (`list_tools`), and invokes them. Holds the domain→URL map (built from manifests). |
| `narration.py` | Turns a structured result + an "event" name into the single spoken sentence. Assembles the event vocabulary from all domain manifests plus host-level events (handoff, out_of_scope, multi). |
| `handoff.py` | The escalation service. Tracks unproductive/out-of-scope strikes and triggers a human handoff after the threshold (e.g. 3 failed slot-fills). Also handles voluntary "get me a human". |
| `session.py` | Per-call state keyed by `call_id` — AccessContext, history, slot accumulators, and the call note. Two backends behind one interface: in-process, or `RedisSessionStore` (`session:<call_id>` Hash, TTL refreshed on write) selected by `REDIS_URL`. With Redis the host is **stateless** — a container restart mid-call doesn't drop the caller, and any replica can serve any turn. |
| `slots.py` | `SlotState` — accumulates a tool's arguments across turns and reports which required args are still missing. The generic mechanism behind all slot-filling (verification's name/dob, an order lookup's order_id, etc.). |

### `src/client/providers/` — the LLM behind the host

| File | What it does |
|------|--------------|
| `interfaces.py` | The provider contract: `classify`, `select_tool`, `plan`, `plan_and_select`, `narrate`, `answer_from_context`, `classify_confirmation`. Any LLM backend implements this. |
| `openai_provider.py` | The concrete OpenAI-backed implementation of all the above (routing, tool selection, planning, narration). |
| `factory.py` | Returns the configured provider instance to the host. |

---

## `src/servers/` — the domains (MCP capability servers)

Each domain is its own MCP server and follows the same four-layer DDD shape.
The host never imports domain internals — it reaches them over MCP.

### Registry & runners

| File | What it does |
|------|--------------|
| `manifests.py` | **The domain registry.** Imports every domain's manifest and lists them. The host reads this to learn every domain's URL, tools, events, and access rules. **Adding a domain = one line here.** |
| `registry.py` | Abstract-factory registry to fetch a domain service by name (no if/elif chains). |
| `run_server.py` | Optional single-process runner (all domains on one MCP server). Normally each domain runs as its own process via its `run.py`. |

### A domain's anatomy (same for patient / order / doctor / verification / kb)

Taking **`order/`** as the example — every domain mirrors this:

```
order/
├── run.py            # the MCP SERVER: @mcp.tool() endpoints (list_orders, lookup_order…),
│                     #   runs on its own port. This is what the host calls over the wire.
├── manifest.py       # the domain's SELF-DESCRIPTION: name, url, description, tools,
│                     #   events, required_args, requires_verification/public. The host
│                     #   configures itself from this — no host code knows "order" exists.
├── domain/           # PURE business core (no I/O, no framework)
│   ├── models.py         #   entities (Order, ownership rules)
│   ├── repository/interfaces.py   #   the data-access contract the domain needs
│   └── service/
│       ├── interfaces.py #   the service contract
│       └── services.py   #   business rules (e.g. a caller only sees their OWN orders)
├── infrastructure/   # the OUTER layer — concrete adapters to the world
│   ├── repositories.py   #   concrete repo (reads the shared DB, TTL-caches)
│   ├── adaptors.py       #   maps raw DB rows ↔ domain models
│   └── application.py     #   dependency-injection wiring (builds the service)
└── application/
    └── mcp_server.py     #   registers the domain's tools onto the MCP server
```

The dependency rule points **inward**: `run.py`/`infrastructure` depend on
`domain`, never the reverse. Swapping the DB or the transport touches only the
outer layers.

### The five domains

| Domain | Port | What it does |
|--------|------|--------------|
| `verification/` | 3001 | Verifies the caller with **role-dependent factors**: a patient by name + DOB (+ clinic if the phone number is unrecognised); a doctor by name + clinic (doctors have no DOB), and a doctor on an unrecognised number is sent to a human. Asserted values are checked then discarded — the session is scoped to the *record's* ids, never the caller's claim. |
| `patient/` | 3002 | Returns the verified caller's own account record (details, plan, contact). Single-tool. |
| `order/` | 3003 | The caller's own orders — list, or look up one by number. Placing an order or clinic restock is a **preview → submit** split: the planner can only reach the preview tool (which writes nothing); the host calls the submit tool after an explicit caller confirmation. A one-pending-order limit per owner is an **atomic** `SET NX` lock in Redis, and an approval webhook releases it. Ownership re-checked server-side on every call. |
| `doctor/` | 3005 | Doctor-as-caller: pulls a patient's report/alerts (care-relationship checked) and places clinic restocks. Patient PHI is fetched over MCP from the patient server, never cached here. |
| `kb/` | 3004 | General product/how-to questions answered from a curated knowledge base via semantic retrieval. Reachable **before** verification (public), with per-entry sensitivity gating. |

### `kb/` extras (retrieval-specific)

| File | What it does |
|------|--------------|
| `infrastructure/knowledge_base/embedding_kb.py` | Builds/loads the KB embeddings (hybrid keyword + semantic, cached to `kb_vectors.npz`, rebuilt on content change). Provides `_semantic_score(query)`. |
| `infrastructure/knowledge_base/*.jsonl` | The knowledge-base entries (question, short_answer, aliases, tags, sensitivity flag). |
| `infrastructure/repositories.py` | Adapts the embedding index to the domain's repository contract (semantic + small keyword bonus, top-k). |
| `domain/service/services.py` | Applies the confidence threshold (weak match → "I don't have that") and the sensitivity gate (sensitive entries need a verified caller); returns the top-k visible answers. |

---

## `src/shared/` — cross-cutting pieces

| File | What it does |
|------|--------------|
| `manifest.py` | The `DomainManifest` dataclass — the shape every domain fills in to describe itself (name, url, description, tools, tool_events, required_args, events, requires_verification, public, identity_source, special_flow). |
| `policy.py` | The central security policy — the hardcoded `PRE_VERIFICATION_ALLOWLIST` of domains reachable by **unverified** callers. A deliberate, auditable review surface; a domain can't self-grant pre-verification access. |
| `security.py` | `AccessContext` — the per-call security state (verified flag, subject id, role, clinic id) and the `grant()` transition. The host injects this identity into every MCP call, stripping anything the LLM supplied for those fields; the domain server re-enforces it. |
| `cache.py` | Key-value cache with TTL, two backends behind `get_cache()`: in-process `SharedCache`, or `RedisCache` (dataclass-aware JSON encode/decode, `set_if_absent` for the atomic pending lock) chosen by `REDIS_URL`. Each domain caches only its own namespace (`patient:*`, `order:*`, `doctor:*`/`clinic:*`); HIT/MISS/EXPIRED logging lives in the cache itself so every domain reports consistently. |
| `database.py` | The single shared mock datastore all domains read from (one store, domains relate by ID). Postgres in production. |
| `repositories.py` | Reusable base repository plumbing (the "write it once" layer) that each domain's concrete repo builds on. |
| `application_base.py` | Shared base for a domain's MCP application service — handles tool registration so each domain only declares its tools. |
| `logging_config.py` | `setup_logging()` — configures the shared log file. Each server process calls it so their logs (cache hits, KB retrievals, routing) land in one place. |
| `tracing.py` | The only file importing MLflow. Wraps turns, MCP tool calls (capturing `args_from_llm` vs injected `identity` vs `args_sent` — the clearest view of the identity guarantee), and provider calls into inspectable traces. Lazy/background init so the ~15s MLflow import never blocks host startup; a no-op when `HEARTBUG_TRACING` is unset. |

---

## `redis/` — session + cache backing store

| File | What it does |
|------|--------------|
| `users.acl` | One Redis user per domain, each restricted to its own key namespace (`patientsvc` → `patient:*`, `ordersvc` → `order:*`/`pending:*`, etc.). Makes "patient data lives in the patient context" an **infrastructure guarantee** — the doctor container gets `NOPERM` trying to read `patient:*`, not just a code convention. **The aclfile format is strict: `user` lines only, no comments.** |
| `README.md` | The ACL design, the per-user namespace table, and the production notes (real secrets, disable the default user). |

Redis runs as a service in `docker-compose.yml` on the `heartbug` network; each
container reaches it by service name (`redis://…@redis:6379`) as its own ACL user.

---

## Other

| File | What it does |
|------|--------------|
| `src/demo_real_mcp.py` | A scripted end-to-end demo call against the real MCP servers (needs `OPENAI_API_KEY`). |
| `docs/DESIGN-self-describing-domains.md` | The architecture design doc — manifests, tool selection, slot-filling, the planner+DAG, security model, and the deferred RAG-for-tools plan. |

---

## Running it

### Docker (the normal way)

Six services (five domains + host) plus Redis, all on one bridge network:

```bash
export OPENAI_API_KEY=...
docker compose build        # needed after any new dependency (mlflow, redis)
docker compose up -d
docker compose logs host | grep SESSIONS      # → "SESSIONS redis → redis://hostsvc:…"
```

Containers reach each other and Redis **by service name**, never `localhost`
(inside a container `localhost` is the container itself). `docker compose logs
<service>` reads each container's own stdout.

### Local (no Docker, 5 terminals)

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...

VERIFICATION_PORT=3001 python -m src.servers.verification.run
PATIENT_PORT=3002      python -m src.servers.patient.run
ORDER_PORT=3003        python -m src.servers.order.run
KB_PORT=3004           python -m src.servers.kb.run
DOCTOR_PORT=3005       python -m src.servers.doctor.run
uvicorn src.client.app:app --port 8000         # the host
```

Without `REDIS_URL` the host and domains fall back to in-process state/cache — fine
for local runs, but a restart loses live calls and caches aren't shared.

### Environment flags

| Var | Effect |
|-----|--------|
| `HEARTBUG_ORCH` | `A` (plan → per-domain tool select) or `B` (one merged call, default). B removes an LLM round trip per turn — lower voice latency. |
| `REDIS_URL` | Session store + cache backend. Unset → in-process fallback. |
| `HEARTBUG_TRACING` | `1` enables MLflow tracing. Start MLflow **before** the stack; the server runs on your Mac (Python 3.12), reached from containers via `host.docker.internal:5001`. |

### Observability

With tracing on, open MLflow (`localhost:5001`) → Traces → a `turn` → the MCP tool
span. `args_from_llm` vs `identity` vs `args_sent` side by side is the clearest
single view of the identity-injection guarantee.

## The one rule that keeps it extensible

Everything the host needs to know about a domain lives in that domain's
`manifest.py`, collected in `servers/manifests.py`. **Add a domain = write its
folder + manifest + one registration line.** The gate, router, planner, tool
selection, and narration all configure themselves from the manifests — no host
file needs editing.
