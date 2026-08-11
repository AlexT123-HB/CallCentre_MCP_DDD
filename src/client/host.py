"""
The LLM HOST — drives a call. ALL reasoning is done by the LLM (no rule-based logic).

Endpoint model:
  POST /webhook            → a call arrives: greeting + provisional phone match
  POST /{domain}           → one caller turn routed to a domain. The LLM router
                             picks the domain; the gate constrains the choice.
                             VERIFICATION is one of the domains (the only one
                             reachable until the caller is verified).

The host never accesses data — every call goes through the MCP client to a domain.
"""

from __future__ import annotations

import os
import asyncio
import json

from src.client import notes
from src.client.slots import SlotState
from src.servers import manifests
from src.client.session import build_session_store
from src.client.mcp_client import MCPClient
from src.client.gate import available_domains, verification_gated_domains
from src.client.router import Router
from src.client.narration import Narrator
from src.client.orchestrator import Orchestrator, DomainCall
from src.client.handoff import HandoffService
from src.client.providers.factory import get_provider
from src.shared.logging_config import setup_logging
from src.shared import tracing


class Host:
    def __init__(self, mcp=None, provider=None, orchestration_mode=None) -> None:
        self.log = setup_logging()                       # → logs/heartbug.log
        # orchestration mode: "A" = plan → select_tool per domain (default),
        # "B" = one merged plan_and_select call. Env HEARTBUG_ORCH overrides.
        self.orchestration_mode = (orchestration_mode
                                   or os.getenv("HEARTBUG_ORCH", "A")).upper()
        # Real MCP: the host is a pure client. It does NOT import or build the
        # domain servers — they run as separate processes reached over the wire.
        self.client = MCPClient()                        # uses DOMAIN_SERVERS map
        self.sessions = build_session_store()
        provider = provider or get_provider()           # LLM provider (injectable)
        self._provider = provider or get_provider()           # LLM provider (injectable)
        self.router = Router(provider)
        self.narrator = Narrator(provider)
        self.orchestrator = Orchestrator(self.client)
        self.handoff = HandoffService()
        self.registry = None                             # kept for gate signature compat

    # ── POST /webhook ─────────────────────────────────────────────────────────
    async def webhook(self, call_id: str, ani: str) -> dict:
        session = self.sessions.start(call_id, ani)
        tracing.set_session(call_id, ani=ani)  # for tracing context propagation in async tasks
        self.log.info(f"[{call_id}] ANI: {ani}")
        # slot accumulators are created lazily per (domain, tool) in _slot()
        # provisional phone match via the verification domain. Over the wire the
        # server can't set the host's ctx, so capture the matched id it returns
        # and thread it into the session for verify_identity to use.
        res = await self.client.call(session, "verification", "lookup_by_phone", phone=ani)
        matched = (res or {}).get("matched_subject_id") or (res or {}).get("subject_id")
        if matched:
            session["ctx"].matched_subject_id = matched
        role = (res or {}).get("role") or "patient"
        tts = self.narrator.say({"event": f"greeting_{role}"}, "")
        self.sessions.save(session)  # save the new session with the matched_subject_id
        self.log.info(f"[{call_id}] SYSTEM: {tts}")
        return {"call_id": call_id, "tts_response": tts}

    # ── POST /{domain} ─────────────────────────────────────────────────────────
    # The host resolves the domain for a turn via the LLM router, constrained by
    # the gate. `domain` in the path is optional: if provided, it's a hint; the
    # gate still governs what's actually reachable.
    @tracing.traced("turn", tracing.AGENT)
    async def turn(self, call_id: str, transcript: str, domain_hint: str | None = None) -> dict:
        """Load the session once, run the turn, persist once.

        The turn body mutates the session in many places (history, ctx via grant(),
        slots, notes, escalation counters) and has several return points. Rather
        than a save() at each, we load here and save in a finally — so EVERY path
        persists, including verification's grant() and the error path. Without this,
        with a Redis-backed store, none of those mutations survive to the next turn:
        verify would set verified=True in memory, the turn would return without
        saving, and the next turn would reload verified=False from Redis (leaving the
        gate stuck at ['verification','kb'])."""
        session = self.sessions.get(call_id)
        try:
            return await self._turn(session, call_id, transcript, domain_hint)
        finally:
            self.sessions.save(session)

    async def _turn(self, session: dict, call_id: str, transcript: str,
                    domain_hint: str | None = None) -> dict:
        # group every turn of this call into one viewable conversation
        ctx0 = session["ctx"]
        tracing.set_session(call_id, role=ctx0.role, verified=ctx0.verified,
                            subject_id=ctx0.subject_id, clinic_id=ctx0.clinic_id)
        tracing.record(transcript=transcript, domain_hint=domain_hint,
                       orchestration_mode=self.orchestration_mode)
        session["history"].append({"role": "user", "content": transcript})

        self.log.info(f"[{call_id}] CALLER: {transcript}")
        self.log.info(f"[{call_id}] Orchestrator: {self.orchestration_mode} mode")

        allowed = available_domains(session, self.registry)              # gate
        candidates = {n: manifests.get(n).description for n in allowed if manifests.get(n)}
         # domains that exist but need verification first (for steering unverified callers)
        gated = verification_gated_domains(session)

        # ── VERIFIED callers use the PLANNER (may split into multi-domain subtasks);
        #    UNVERIFIED callers use single-domain routing (only verification allowed) ──
        if session["ctx"].verified:
            # ── CONFIRM: a sensitive write is waiting for the caller's reply. Catch
            #    it BEFORE planning so the reply doesn't get re-routed. Fully generic:
            #    the tool's manifest `confirmations` contract declares the valid
            #    answers and what each does; the host classifies + executes, knowing
            #    nothing domain-specific. ──
            confirm = session.get("_pending_confirm")
            if confirm:
                answers = confirm.get("answers", {})
                decision = self._provider.classify_confirmation(
                    transcript, confirm.get("prompt", ""), session["history"], answers)
                session.pop("_pending_confirm", None)
                if decision in answers:
                    self.handoff.record_task_outcome(session, "confirmation", True)
                    notes.record_action(session, f"caller answered '{decision}' to the {confirm['tool']} confirmation")  # working memory for later turns
                    result = await self._run_confirmation_action(
                        session, confirm, answers[decision], transcript)
                    tts = result["tts"]
                    session["history"].append({"role": "assistant", "content": tts})
                    self.log.info(f"[{call_id}] SYSTEM: {tts}")
                    out = {"call_id": call_id, "tts_response": tts, "domain": confirm["domain"]}
                    if result.get("transfer"):
                        out["action"] = "transfer"
                    return out
                # unclear / unmatched → drop the confirmation, fall through to planning
                else:
                    # couldn't classify the reply into any declared answer — an
                    # unproductive turn. Repeats mean we're talking past each other,
                    # so the existing threshold escalates to a human.
                    self.handoff.record_task_outcome(session, "confirmation", False)
                    action = self.handoff.check(session)
                    if action:
                        return self._handoff_response(call_id, session, action, transcript)

            # ── RESUME: a pinned plan is waiting for slot-fill? ──
            # If the caller is still on it, fill slots WITHOUT re-planning/re-selecting
            # (prevents loops). If they've clearly redirected (this turn routes to a
            # different domain), DROP the pin and re-plan fresh — the redirect resets
            # the stuck counter via the normal productive-advance path.
            pending = session.get("_pending")
            if pending and self._still_on_pending(pending, transcript, allowed, session):
                tts = await self._run_plan(session, pending, transcript)
                domain = pending[0]["domain"] if len(pending) == 1 else "multi"
            else:
                if pending:
                    session.pop("_pending", None)          # redirect → abandon pin
                small_talk = False
                from_context = False
                if self.orchestration_mode == "B":
                    domain_tools = await self._domain_tools_map(allowed)
                    ps = self._provider.plan_and_select(transcript, domain_tools, self._with_note(session))
                    if ps.get("connect_human"):
                        action = self.handoff.check(session, voluntary=True)
                        return self._handoff_response(call_id, session, action, transcript)
                    small_talk = ps.get("small_talk")
                    from_context = ps.get("answer_from_context")
                    subtasks = self._validate_plan(ps.get("subtasks", []), allowed)
                    self.log.info(f"[{call_id}] PLAN+SELECT allowed={allowed} → "
                                  f"{[(s['id'], s['domain'], s.get('tool')) for s in subtasks]}")
                else:
                    # Version A: plan → select_tool per domain 
                    plan = self._provider.plan(transcript, candidates, self._with_note(session))
                    if plan.get("connect_human"):
                        action = self.handoff.check(session, voluntary=True)
                        return self._handoff_response(call_id, session, action, transcript)
                    small_talk = plan.get("small_talk")
                    from_context = plan.get("answer_from_context")
                    subtasks = self._validate_plan(plan.get("subtasks", []), allowed)
                    self.log.info(f"[{call_id}] PLAN allowed={allowed} → "
                                  f"{[(s['id'], s['domain']) for s in subtasks]}")
                    subtasks = await self._resolve_subtasks_A(session, subtasks, transcript)

                if subtasks:
                    self.handoff.reset_out_of_scope(session)
                    tts = await self._run_plan(session, subtasks, transcript)
                    domain = subtasks[0]["domain"] if len(subtasks) == 1 else "multi"
                elif small_talk:
                     # purely social turn, no task — answer warmly, don't dispatch, and
                    # don't count it as an out-of-scope strike toward handoff
                    self.handoff.reset_out_of_scope(session)
                    tts = self.narrator.say({"event": "small_talk"}, transcript)
                    self.log.info(f"[{call_id}] SMALL-TALK")
                    domain = "small_talk"
                elif from_context:
                     # the caller is asking about something already established this call
                    # (a detail already given, an action already done) — answer from the
                    # conversation, no tool. Counts as productive (resets the strike).
                    self.handoff.reset_out_of_scope(session)
                    tts = self.narrator.answer_from_context(transcript, self._with_note(session))
                    self.log.info(f"[{call_id}] ANSWER-FROM-CONTEXT")
                    domain = "context"
                else:
                    self.handoff.record_out_of_scope(session)
                    tts = self.narrator.say({"event": "out_of_scope"}, transcript)
                    domain = "unknown"
        else:
            # ── RESUME a pinned verification slot-fill (name/dob) without re-routing ──
            pending = session.get("_pending")
            if pending and pending[0]["domain"] in allowed:
                tts = await self._run_plan(session, pending, transcript)
                domain = pending[0]["domain"]
            else:
                routed = self.router.classify_turn(transcript, candidates, session["history"], gated_domains=gated)
                domain = routed["domain"]
                self.log.info(
                    f"[{call_id}] ROUTER allowed={allowed} → domain={domain} "
                    f"conf={routed.get('confidence')} connect_human={routed.get('connect_human')}")
                if routed.get("connect_human"):
                    action = self.handoff.check(session, voluntary=True)
                    return self._handoff_response(call_id, session, action, transcript)
                if domain != "unknown" and domain not in allowed:
                    domain = "unknown"                 # disallowed pick → out of scope
                if domain == "unknown":
                    self.handoff.record_out_of_scope(session)
                    tts = self.narrator.say({"event": "out_of_scope"}, transcript)
                else:
                    self.handoff.reset_out_of_scope(session)
                    # resolve to a single subtask and run through the SHARED pipeline
                    st = [{"id": "s1", "domain": domain, "intent": transcript, "depends_on": []}]
                    st = await self._resolve_subtasks_A(session, st, transcript)
                    tts = await self._run_plan(session, st, transcript)

        # ── INVOLUNTARY handoff: any counter over threshold? ──
        action = self.handoff.check(session)
        if action:
            return self._handoff_response(call_id, session, action, transcript)

        session["history"].append({"role": "assistant", "content": tts})
        self.log.info(f"[{call_id}] SYSTEM: {tts}")
        return {"call_id": call_id, "domain": domain, "tts_response": tts}

    # ── build the caller-facing handoff response ──────────────────────────────
    def _handoff_response(self, call_id, session, action, transcript) -> dict:
        tts = self.narrator.say(
            {"event": "handoff", "queue": action["queue"], "reason": action["reason"]},
            transcript)
        session["history"].append({"role": "assistant", "content": tts})
        self.log.info(f"[{call_id}] SYSTEM: {tts}")
        return {"call_id": call_id, "action": action["action"], "queue": action["queue"],
                "reason": action["reason"], "tts_response": tts}

    # ── verification domain flow (slot-filling) ────────────────────────────────
    # identity params the HOST injects (never extracted by the LLM)
    _IDENTITY_PARAMS = {"call_id", "verified", "subject_id", "role", "clinic_id", "matched_subject_id"}


    async def _after_verify(self, session: dict, tool: str, res: dict, transcript: str) -> str:
        """Verification-only post-success: promote the session context + prewarm caches."""
        call_id = session["call_id"]
        self.log.info(f"[{call_id}] VERIFICATION verified={res.get('verified')} "
                      f"subject_id={res.get('subject_id')} reason={res.get('reason')} role={res.get('role')} clinic_id={res.get('clinic_id')}")
        slot = self._slot(session, "verification", tool)
        if res.get("verified"):
            session["ctx"].grant(res["subject_id"], role=res.get("role", "patient"),
                                 clinic_id=res.get("clinic_id"))
            self.handoff.reset_verify(session)
            notes.record_action(session, f"caller verified as a {res.get('role')}")
            name = str(slot.values.get("name", ""))
            slot.reset()
            for mm in manifests.all_manifests():                # prewarm caches
                if "prewarm" in (mm.tools or []):
                    try:
                        await self.client.call(session, mm.name, "prewarm")
                    except Exception:
                        pass
            first = name.split()[0] if name else ""
            return self.narrator.say({"event": "verified", "caller_name": first}, transcript)

        # a doctor off their registered number has no second factor (no DOB on
        # record) → no self-service path, hand to a human
        if res.get("connect_human"):
            self.log.info(f"[{call_id}] VERIFY connect_human → unregistered number")
            self.handoff.check(session, voluntary=True)
            return self.narrator.say({"event": "verify_escalate"}, transcript)

        # name matched but we still need this ROLE's second factor (dob | clinic).
        # Keep the slots — the name is good, only the second factor is missing.
        expect = res.get("expect")
        if expect:
            return self.narrator.say(
                {"event": "ask_clinic" if expect == "clinic" else "ask_dob"}, transcript)

        # wrong values → count toward the stuck counter, reset slots, ask again
        self.handoff.record_verify_unproductive(session)
        slot.reset()
        return self.narrator.say({"event": "verify_failed"}, transcript)

    def _slot(self, session: dict, domain: str, tool: str):
        """Get (or create) the per-(domain,tool) slot accumulator for this session."""
        slots = session.setdefault("_slots", {})
        key = f"{domain}:{tool}"
        if key not in slots:
            slots[key] = SlotState(domain, tool)
        return slots[key]

    async def _extract_args(self, session: dict, domain: str, tool: str, transcript: str) -> dict:
        """Single-tool domains still extract the tool's args via the selector (so
        verification's name/dob get extracted even though there's one tool)."""
        candidates = await self._candidate_tools(domain)
        if not candidates.get(tool, {}).get("args"):
            return {}                                           # tool takes no business args
        sel = self._provider.select_tool(transcript, candidates, self._with_note(session))
        return sel.get("args", {}) or {}

    async def _candidate_tools(self, domain: str) -> dict:
        """Discover a domain's tools and shape them for the selector, with the
        host-injected identity params filtered out (LLM only sees business args).

        Only tools the domain's MANIFEST declares selectable are offered. The MCP
        server exposes EVERY @mcp.tool() via list_tools — including confirmation-only
        writes like submit_order and webhook-only tools like lookup_by_phone — so
        without this filter the selector could pick a tool the manifest deliberately
        withheld (and hallucinate its args). This is what let a mis-planned "thank
        you" turn select submit_order with invented args."""
        m = manifests.get(domain)
        selectable = set(m.tools or []) if m else None
        discovered = await self.client.discover_tools(domain)
        out: dict = {}
        for t in discovered:
            name = getattr(t, "name", None)
            if not name or name == "prewarm":
                continue
            schema = getattr(t, "inputSchema", {}) or {}
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            args = {a: (props[a].get("description", a) if isinstance(props[a], dict) else a)
                    for a in props if a not in self._IDENTITY_PARAMS}
            out[name] = {"description": getattr(t, "description", "") or "", "args": args}
        return out
    
    def _with_note(self, session: dict) -> list:
        """History plus the call note. The note goes LAST so the provider's
        `history[-N:]` window can never slice it away — that window is exactly what
        loses the early turns the note exists to preserve."""
        history = session["history"]
        block = notes.render(session)
        return history + [{"role": "system", "content": block}] if block else history 

    async def _domain_tools_map(self, domains: list) -> dict:
        """Gather every allowed domain's selectable tools (via discovery) for the
        merged Version-B call. {domain: {description, tools:{...}}}."""
        out = {}
        for d in domains:
            m = manifests.get(d)
            if not m:
                continue
            out[d] = {"description": m.description,
                      "tools": await self._candidate_tools(d)}
        return out

    # ══ UNIFIED PLAN PIPELINE ════════════════════════════════════════════════
    # Both orchestration modes (A: plan→select per subtask, B: merged plan+select)
    # resolve a request into the SAME structure — a list of subtasks each carrying
    # {id, domain, tool, args, depends_on}. From there, ONE shared pipeline runs:
    #   slot-fill (pin the plan across turns until required args are gathered)
    #   → execute (parallel/sequential DAG) → narrate.
    # This makes slot-filling + the 3-strikes handoff work identically for single
    # OR multi subtask plans, in mode A OR B — no path bypasses them.

    async def _run_plan(self, session: dict, subtasks: list, transcript: str) -> str:
        """Slot-fill (pinning across turns) then execute a resolved subtask list."""
        call_id = session["call_id"]

        # ── SLOT-FILLING PHASE ──
        # Walk subtasks in dependency order; for each, accumulate its tool's args
        # and check required_args. The FIRST subtask still missing a required arg
        # pins the whole plan and asks for that arg (one ask per turn). Args that
        # come from a prior step's RESULT (depends_on) are not slot-filled — they're
        # wired at execution — so only CALLER-supplied args gate here.
        for st in self._in_dep_order(subtasks):
            domain, tool = st["domain"], st.get("tool")
            m = manifests.get(domain)
            if not m or not tool:
                continue
            if st.get("depends_on"):
                continue                       # args come from upstream result, not caller
            required_map = (m.required_args or {}).get(tool, {})
            if not required_map:
                continue
            slot = self._slot(session, domain, tool)
            slot.update(st.get("args", {}) or {})              # args resolved at plan time
            # also extract from THIS turn's input (so a resume turn like "David Chen"
            # or "1965-05-08" fills the pending slot — the pin never re-plans/re-selects)
            if slot.missing(list(required_map.keys())):
                fresh = await self._extract_args(session, domain, tool, transcript)
                slot.update(fresh)
            missing = slot.missing(list(required_map.keys()))
            self.log.info(f"[{call_id}] SLOTS domain={domain} tool={tool} "
                          f"have={slot.values} missing={missing}")
            if missing:
                # pin the resolved plan so the next turn RESUMES it (no re-plan,
                # no re-select → no loop), and ask for the one missing arg.
                session["_pending"] = subtasks
                self.handoff.record_verify_unproductive(session)   # unproductive ask
                return self.narrator.say({"event": required_map[missing]}, transcript)

        # ── all caller-args gathered → the plan is complete: clear the pin ──
        session.pop("_pending", None)
        self.handoff.reset_verify(session)     # productive: slots filled

        # inject the gathered slot values into each subtask's args
        for st in subtasks:
            domain, tool = st["domain"], st.get("tool")
            if tool and not st.get("depends_on"):
                slot = self._slot(session, domain, tool)
                merged = dict(st.get("args", {}) or {})   # keep LLM-extracted args (query!)
                merged.update(slot.values)                # add slot-filled args on top
                st["args"] = merged

        # ── EXECUTION PHASE (DAG: parallel + sequential) ──
        if len(subtasks) == 1 and not subtasks[0].get("depends_on"):
            return await self._execute_single(session, subtasks[0], transcript)
        return await self._execute_dag(session, subtasks, transcript)

    def _in_dep_order(self, subtasks: list) -> list:
        """Subtasks with no deps first (their caller-args gate slot-filling)."""
        return sorted(subtasks, key=lambda s: len(s.get("depends_on", [])))
    
    async def _run_confirmation_action(self, session: dict, confirm: dict,
                                       spec: dict, transcript: str) -> dict:
        """Execute the manifest-declared action for the caller's confirmation reply.
        Three generic action types — the host has NO domain knowledge:
          call_tool → dispatch a host-only tool (e.g. a submit tool) with the carried
                      args, then narrate its result. Submit tools aren't planner-
                      selectable, so only this path can trigger a write.
          narrate   → narrate an event, no side effect (e.g. a cancellation).
          escalate  → narrate an event AND hand off to a human (action:"transfer")."""
        kind = spec["action"]
        domain = confirm["domain"]

        if kind == "call_tool":
            tool = spec["tool"]
            args = {**confirm["args"]}
            agg = await self.orchestrator.dispatch(
                session, [DomainCall(domain, tool, args)])
            res = agg.results.get(self.orchestrator.key_for(domain, tool), {})
            self.handoff.record_task_outcome(session, tool, bool(res.get("found")))
            tts = self.narrator.say({**res}, transcript) if res.get("event") \
                else self.narrator.say({"event": "out_of_scope"}, transcript)
            return {"tts": tts}

        if kind == "escalate":
            self.log.info(f"[{session['call_id']}] CONFIRM escalate → {spec['event']}")
            tts = self.narrator.say({"event": spec["event"]}, transcript)
            return {"tts": tts, "transfer": True}

        # kind == "narrate"
        tts = self.narrator.say({"event": spec["event"]}, transcript)
        return {"tts": tts}

    async def _execute_single(self, session: dict, st: dict, transcript: str) -> str:
        """Dispatch one fully-resolved subtask and narrate — includes verification's
        post-success step and the multi-outcome (result-supplied event) narration."""
        call_id = session["call_id"]
        domain, tool = st["domain"], st["tool"]
        m = manifests.get(domain)
        agg = await self.orchestrator.dispatch(
            session, [DomainCall(domain, tool, st.get("args", {}) or {})])
        res = agg.results.get(self.orchestrator.key_for(domain, tool), {})
        success = bool(res.get("found") or res.get("verified"))
        self.handoff.record_task_outcome(session, tool, success)
        if success:
            notes.record_result(session, tool, res)      # working memory for later turns

        if m.special_flow == "verification":
            return await self._after_verify(session, tool, res, transcript)
        if res.get("event"):                                   # domain-supplied event
            self.handoff.reset_out_of_scope(session)
            needs_confirm = (res.get("needs_confirmation")
                             or str(res.get("event", "")).endswith("_needs_approval"))
            tts = self.narrator.say({**res}, transcript, self._with_note(session))
            if needs_confirm:
                contract = ((m.confirmations or {}).get(tool) or {}).get("answers", {})
                session["_pending_confirm"] = {
                    "domain": domain, "tool": tool,
                    "args": {**(st.get("args") or {})},
                    "answers": contract,     # manifest-declared: label → {action, tool/event, hint}
                    "prompt": tts,           # what we asked → context for the classifier
                }
            return tts
        if success:
            self.handoff.reset_out_of_scope(session)
            event = (m.tool_events or {}).get(tool, "out_of_scope")
            return self.narrator.say({"event": event, **res}, transcript, self._with_note(session))
        # Tool found nothing — assign event is "not_found"/"{domain}_not_found".
        domain_nf = f"{domain}_not_found"
        nf_event = domain_nf if domain_nf in (m.events or {}) else "not_found"
        return self.narrator.say({"event": nf_event, "what": tool}, transcript,
                                 self._with_note(session))

    async def _execute_dag(self, session: dict, subtasks: list, transcript: str) -> str:
        """Run multi-subtask plans: ready steps concurrently, prior results wired
        into dependents, then narrate the combined result."""
        steps = {s["id"]: s for s in subtasks}
        results, done = {}, set()
        while len(done) < len(steps):
            ready = [s for sid, s in steps.items()
                     if sid not in done and all(d in done for d in s.get("depends_on", []))]
            if not ready:
                break
            calls = []
            for s in ready:
                if s.get("depends_on"):
                    # dependent: wire prior results into this subtask's args
                    prior = {d: results[d] for d in s["depends_on"]}
                    call = await self._build_call(session, s, prior)
                    if call:
                        s["tool"] = call.tool      # narration keys off the step; a
                        s["args"] = call.args      # dependent tool resolves only here
                        calls.append((s["id"], s["domain"], call))
                else:
                    calls.append((s["id"], s["domain"],
                                  DomainCall(s["domain"], s["tool"], s.get("args", {}) or {})))
            agg = await self.orchestrator.dispatch(session, [c for _, _, c in calls])
            for sid, dom, call in calls:
                # look up by domain.tool: two subtasks can share a domain
                results[sid] = agg.results.get(
                    self.orchestrator.key_for(call.domain, call.tool), {})
                if results[sid].get("found"):
                    notes.record_result(session, call.tool, results[sid])
                self.handoff.record_task_outcome(session, dom, bool(results[sid].get("found")))
            for s in ready:
                done.add(s["id"])
        return self._narrate_multi(results, steps, transcript)

    async def _resolve_subtasks_A(self, session: dict, subtasks: list, transcript: str) -> list:
        """Version A: subtasks carry domains only. Resolve each independent subtask's
        tool + args via select_tool (dependent ones resolve at execution from prior
        results). Produces the same {domain,tool,args,depends_on} structure as B."""
        for st in subtasks:
            if st.get("depends_on"):
                continue                       # resolved later from upstream result
            domain = st["domain"]
            m = manifests.get(domain)
            if not m:
                continue
            tools = [t for t in (m.tools or []) if t != "prewarm"]
            if len(tools) == 1:
                st["tool"] = tools[0]
                st["args"] = await self._extract_args(session, domain, tools[0],
                                                      st.get("intent", transcript))
            elif tools:
                cands = await self._candidate_tools(domain)
                sel = self._provider.select_tool(st.get("intent", transcript), cands, self._with_note(session))
                st["tool"] = sel.get("tool") if sel.get("tool") in tools else m.primary_tool
                st["args"] = sel.get("args", {}) or {}
        return subtasks

    def _still_on_pending(self, pending: list, transcript: str, allowed, session: dict) -> bool:
        """Is this turn still about the pinned plan, or did the caller redirect?
        Cheap signal: route the turn; if it clearly matches a DIFFERENT allowed
        domain than the pinned one(s), treat it as a redirect (drop the pin). A
        non-answer or same-domain reply stays on the pinned plan. Errs toward
        staying (a short slot-fill answer like 'O1003' won't classify as a new
        domain, so it correctly continues)."""
        pending_domains = {s["domain"] for s in pending}
        candidates = {n: manifests.get(n).description for n in allowed if manifests.get(n)}
        routed = self.router.classify_turn(transcript, candidates, session["history"], gated_domains=verification_gated_domains(session))
        picked = routed.get("domain")
        # a confident pick of a DIFFERENT real domain → redirect
        if (picked and picked != "unknown" and picked not in pending_domains
                and routed.get("confidence", 0) >= 0.6):
            self.log.info(f"[{session['call_id']}] REDIRECT off pinned {pending_domains} "
                          f"→ {picked}; dropping pin")
            return False
        return True


    def _validate_plan(self, subtasks: list, allowed) -> list:
        """Sanity-filter the LLM's plan: drop disallowed domains, bad depends_on
        references, and dedupe. (Authorization is enforced here; the gate already
        excludes verification post-verify so it can't appear.)"""
        ids = {s.get("id") for s in subtasks}
        out, seen = [], set()
        for s in subtasks:
            d = s.get("domain")
            if d not in allowed:
                continue
            fingerprint = (d, s.get("tool"), json.dumps(s.get("args") or {}, sort_keys=True))
            if fingerprint in seen:
                continue
            if any(dep not in ids for dep in s.get("depends_on", [])):
                continue
            seen.add(fingerprint)
            out.append(s)
        return out


    async def _build_call(self, session: dict, subtask: dict, prior_results: dict):
        """Resolve one subtask to a DomainCall. For dependents, the prior step's
        result is fed as context so select_tool extracts the needed args from it
        (same generic extraction used everywhere) — no output schemas needed."""
        domain = subtask["domain"]
        m = manifests.get(domain)
        if not m:
            return None
        tools = [t for t in (m.tools or []) if t != "prewarm"]
        if not tools:
            return None
        context = subtask.get("intent", "")
        if prior_results:                                      # data-wiring
            context += "\n\nprevious step results: " + json.dumps(prior_results, default=str)

        planned = subtask.get("tool")
        if planned in tools:
            # Mode B: the planner already chose this tool. Keep it — use the prior
            # result only to fill ARGS, never to re-pick the tool. Re-selecting here
            # let select_tool override the plan (e.g. collapsing send_patient_report
            # back into a second check_patient_status), silently executing a
            # different plan than the one produced.
            tool = planned
            args = await self._extract_args(session, domain, tool, context)
        elif len(tools) == 1:
            tool = tools[0]
            args = await self._extract_args(session, domain, tool, context)
        else:
            # Mode A: dependent subtask carries only an intent, so select now.
            cands = await self._candidate_tools(domain)
            sel = self._provider.select_tool(context, cands, self._with_note(session))
            tool = sel.get("tool") if sel.get("tool") in tools else m.primary_tool
            args = sel.get("args", {}) or {}
        return DomainCall(domain, tool, args)

    def _narrate_multi(self, results: dict, steps: dict, transcript: str) -> str:
        """Combine all subtask results into one reply; flag any that failed.
 
        Keyed by "<domain>.<tool>", NOT by domain: one turn can call several tools in
        the same domain ("how is Alex doing and any alerts?"), and keying by domain
        alone dropped every result but the last — the caller then heard only half an
        answer. Failures are named the same way so the narration can say exactly which
        part didn't come back."""
        by_call, failures = {}, []
        for sid, res in results.items():
            step = steps[sid]
            key = f"{step['domain']}.{step.get('tool', '?')}"
            if res.get("found") or res.get("verified"):
                by_call[key] = res
            else:
                failures.append(key)
        # not-run steps (dependency failed) also count as failures
        for sid, s in steps.items():
            if sid not in results:
                failures.append(f"{s['domain']}.{s.get('tool', '?')}")
        return self.narrator.say(
            {"event": "multi", "results": by_call, "failures": failures}, transcript)