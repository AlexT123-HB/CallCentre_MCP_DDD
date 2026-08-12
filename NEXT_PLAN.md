# NEXT_PLAN

Backlog of planned additions, to work through step by step later. Derived from a
scorecard against a 10-layer agentic-architecture maturity model (dev.to,
Sreeni Ramadurai, "Architecting Agentic AI Applications"). HeartBug scored as a
solid Level 2-3 (stateful tool-calling agent) — which is where the value lives;
we deliberately do NOT climb to Level 5 (multi-agent). These are the level-
appropriate gaps, in priority order. Nothing here is started; each is a "consider
next" item, not a commitment.

Context that matters when picking these up:
- Voice line → latency is the hard constraint; a hung call is dead air.
- Clinical line → PHI/isolation burden is real; memory features stay bounded.
- We already have: MiniLM embeddings (KB), Redis (R1-R6 caches), faithfulness
  loss, rules engine, MCP domain servers, subtask decomposition with depends_on.

---

## HIGH PRIORITY

### 1. Semantic cache (query -> response)   [Layer 6]
**What:** A cache in FRONT of the whole turn pipeline. Embed the caller's query,
compare (cosine) against recent answered queries; on a hit above ~0.92, return the
cached narration directly — no LLM call, no plan, no tool dispatch.
**Why:** Highest ROI. Repeat FAQ-style questions ("does HeartBug work on planes",
"how long does it record") hit the full pipeline every time today. A semantic cache
skips all of it on a hit.
**We already have the primitive:** MiniLM (all-MiniLM-L6-v2) is already loaded for
KB. Reuse it for the query embedding.
**Design sketch:** query -> embed -> nearest cached (query, narration) -> if
cos >= 0.92 return cached narration, else fall through to the normal turn and
write (query, narration) back after. Scope the cache carefully — a KB/FAQ answer
is safe to share; anything patient-specific (order status, verification) must NOT
be semantically cached across callers. Start KB-only.
**Caution:** never semantic-cache patient-specific or verification answers — only
generic KB/FAQ. Cross-caller reuse of patient data = PHI leak.

### 2. Parallel subtask execution (fan-out)   [Layer 7]
**What:** Execute independent subtasks concurrently instead of serially. Total
time becomes the longest subtask, not the sum.
**Why:** Real voice-latency win. The compound-query case ("where's my order AND
does it work on a plane") runs order + KB — independent, but currently likely
serial.
**We already have the primitive:** subtasks carry `depends_on`. Fan out the ones
with empty `depends_on`; keep dependent ones ordered.
**Design sketch:** in `_run_plan`, group subtasks by dependency; `asyncio.gather`
the independent group. Preserve per-subtask error handling (see item 3).

### 3. Timeouts + circuit breaker   [Layer 10]
**What:** Bound every MCP tool call with a timeout; open a circuit breaker on a
domain that is failing, so we stop calling a dead server and degrade gracefully.
**Why:** Sri's open item (orchestrator single-point-of-failure). A hung MCP call
is dead air on a phone line — the worst failure mode here.
**Design sketch:** wrap `orchestrator.dispatch` per-subtask in a timeout; on
timeout, return a structured "no result" for that subtask (feeds item: retry-as-
question, already planned separately) rather than hanging the turn. Circuit
breaker = after N consecutive failures on a domain, short-circuit to escalate.
**Related, already-noted:** the "rephrase failed subtask as a follow-up question"
retry logic (from an earlier Sri meeting) pairs with this — a timed-out subtask
becomes a retry-as-question, not a bare "no information".

---

## MEDIUM PRIORITY (decide deliberately)

### 4. Tool idempotency audit   [Layer 2]
**What:** Confirm a retried write (esp. `submit_order`) cannot double-book.
**Why:** Retries happen (network, timeout). Duplicate side effects = incident.
**Status of what we have:** `_acquire_pending` (SET NX) protects against duplicate
*pending* orders. Need to confirm the *submit* path is idempotent on retry too.
**Action:** audit each write tool; add an idempotency key where a retry could
duplicate a side effect.

### 5. Hybrid retrieval + rerank   [Layer 4]
**What:** Combine dense (vector) + sparse (keyword) retrieval, then rerank.
**Why:** Pure vector search misses exact-match terms — order references, device
model numbers, error codes. The b77c airplane-mode investigation suggests KB
retrieval quality is a real pain point.
**Gate:** ONLY if KB retrieval quality is measurably failing on exact-match terms.
Don't build speculatively — measure recall first (see item 8).
**Design sketch:** add keyword search alongside the MiniLM vector search; merge
candidates; rerank (cross-encoder or a cheap scorer). Keep it off the latency
path if possible, or cache (item 1).

### 6. Structured escalation packet   [Layer 7/8]
**What:** Enrich human handoffs with: context summary, recommended action,
confidence signal, supporting evidence, risk explanation.
**Why:** Today's escalation is barer than this. A structured packet lets a human
decide in under a minute — matters for the clinical-desk / patient-support handoff.
**Design sketch:** on escalate, assemble the packet from session (history summary,
the triggering rule/reason, confidence, the tool results so far). Attach to the
handoff event.

### 7. Input injection / jailbreak screening   [Layer 8]
**What:** Screen caller input (and KB-sourced content) for prompt-injection and
jailbreak attempts before it reaches reasoning.
**Why:** A phone line takes arbitrary caller speech; INDIRECT injection via KB
content is a real vector (a poisoned KB entry steering the planner). We're strong
on output guardrails (faithfulness loss, rules, PII mask) but light on input
screening.
**Design sketch:** cheap input classifier / keyword pre-filter at pre_plan;
sanitize retrieved KB content before it influences tool decisions. Keep it fast
(latency path). Note: our verification gate already limits blast radius (an
unverified caller reaches only verification + KB).

---

## OBSERVABILITY GAP (fold into whichever item touches it)

### 8. RAG retrieval-recall metric   [Layer 9]
**What:** Measure KB retrieval quality over time (recall, grounded-answer rate).
**Why:** Retrieval quality "silently decays" as the KB changes; we don't track it.
Also the prerequisite gate for item 5 (don't add hybrid/rerank without measuring
the problem first).
**Design sketch:** log per-KB-turn the retrieved-chunk relevance (we already have
the req<->tool cosine from the faithfulness diag); aggregate as a recall proxy.

### 9. Cache-hit-rate metric   [Layer 9]  — PARTLY DONE / VALIDATED
We just learned cached_tokens was ~0 (prompt-cache below OpenAI's 1024-token floor;
tool-order nondeterminism also fixed). The guide calls falling cache-hit-rate "the
earliest warning signal." Action: log cached_tokens / prompt_tokens as a first-
class metric so prompt-cache regressions surface immediately. When item 1 (semantic
cache) lands, add its hit-rate too.

---

## DELIBERATE NON-GOALS (do NOT build — wrong level for us)

- **Multi-agent / A2A / specialist agents (Level 5).** We are single-orchestrator
  by design. The guide's #1 named mistake is jumping to Level 5 before Level 2/3
  is solid. Revisit ONLY if a domain needs autonomous reasoning (clinical triage
  is the one plausible future trigger).
- **Skills.md modular prompts.** Overkill for 5 domains.
- **Full episodic/procedural long-term memory (Level 4/5).** Clinical-line PHI
  isolation burden is real (the guide: LTM isolation must be enforced at the DB
  layer, not app code). Sri's bounded TTL'd session-reuse ("timed memory") is the
  right first step, not the full three-type LTM. Keep memory a deliberate,
  bounded decision.

---

## SEQUENCING NOTE
Suggested order when we start: 3 (timeouts — safety first, and Sri's open item)
-> 2 (parallel — latency, low risk) -> 1 (semantic cache — highest ROI, but needs
careful PHI scoping) -> then medium items as measurement justifies (5 gated on 8).
Each is independent; none blocks the others hard.
