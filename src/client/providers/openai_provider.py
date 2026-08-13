"""
OpenAI implementation of LLMProvider.

All intent classification, entity extraction, and narration are performed by the
LLM via structured prompts. JSON-mode is used for classification so the result
parses reliably.

Requires: OPENAI_API_KEY in the environment, and `pip install openai`.
Model is configurable via HEARTBUG_MODEL (default a small, cheap model).
"""

from __future__ import annotations

import json
import logging
import os
from typing import List
from openai import OpenAI 

from src.client.providers.interfaces import LLMProvider
from src.shared import tracing


def _domain_items(candidate_domains):
    """Normalize candidate_domains to (name, description) pairs.

    Accepts either a {name: description} map (from manifests) or a plain list of
    names (older callers / tests), so the prompt builder works with both.
    """
    if isinstance(candidate_domains, dict):
        return list(candidate_domains.items())
    return [(name, "") for name in candidate_domains]

HISTORY_WINDOW = 8
class OpenAIProvider(LLMProvider):
    def __init__(self, model: str | None = None) -> None:
                             # imported lazily
        self._client = OpenAI()                        # reads OPENAI_API_KEY
        self._model = model or os.getenv("HEARTBUG_MODEL", "gpt-5.4-mini")

    def _complete(self, **kwargs):
        """Single choke point for every LLM call. Runs the completion, then records
        token usage on the current trace span — so cost/token capture lives in ONE
        place instead of being copy-pasted (and forgotten) at each call site."""
        resp = self._client.chat.completions.create(model=self._model, **kwargs)
        tracing.record_usage(getattr(resp, "usage", None), model=self._model)
        return resp

    # ── classification: LLM picks the domain + extracts identity slots ────────
    @tracing.traced("llm.classify_confirmation", tracing.LLM)
    def classify_confirmation(self, call_id: str, transcript: str, prompt: str, history: list,
                              answers: dict) -> str:
        """Map a caller's confirmation reply to one of the DECLARED answer labels
        (or 'unclear'). Labels and meanings come from the tool's manifest
        `confirmations` contract — no hardcoded label set, so any domain works
        unchanged. `answers` = {label: {"hint": "...", ...}}. Flow-control only."""
        label_lines = "".join(
            f"  {label:<16}— {spec.get('hint', label)}\n"
            for label, spec in answers.items())
        valid = set(answers) | {"unclear"}
        system = (
            "You interpret a caller's reply to a confirmation on a HeartBug phone "
            "call. The assistant just asked the caller to confirm this:\n"
            f'  "{prompt}"\n\n'
            "Classify the caller's reply into exactly one of these labels:\n"
            f"{label_lines}"
            "  unclear         — anything ambiguous, off-topic, or a new question\n"
            'Pick the single best-matching label. Respond ONLY with JSON: '
            '{"decision": "<label>"}'
        )
        msgs = [{"role": "system", "content": system}]
        for h in history[-HISTORY_WINDOW:]:
            msgs.append(h)
        msgs.append({"role": "user", "content": transcript})
        resp = self._complete(messages=msgs,
            response_format={"type": "json_object"}, temperature=0, max_completion_tokens=40)
        raw = resp.choices[0].message.content or "{}"
        logging.getLogger("heartbug").info(f"[{call_id}] CONFIRM llm-raw: {raw}")
        try:
            decision = json.loads(raw).get("decision", "unclear")
        except json.JSONDecodeError:
            decision = "unclear"
        return decision if decision in valid else "unclear"

   # ── classification: LLM picks the domain + extracts identity slots ────────
    @tracing.traced("llm.route", tracing.LLM)
    def classify(self, call_id: str, transcript: str, candidate_domains, history: list = None, gated_domains=None) -> dict:
        domain_block = "".join(f"  - {n}: {d}\n" for n, d in _domain_items(candidate_domains))
        gated_list = "".join(f"  - {n}: {d}\n" for n, d in _domain_items(gated_domains or {}))

        system = (
            "You are the routing brain of the HeartBug healthcare phone assistant. "
            "Classify the caller's latest message into exactly one DOMAIN from the "
            "allowed list. Do NOT extract names, dates, or any other details — only "
            "choose the domain.\n"
            "Allowed domains (choose exactly one by name, or \"unknown\" if none fit):\n"
            + domain_block
            + (
                "\nThe caller is NOT yet verified. These capabilities require identity "
                "verification FIRST (they are NOT in the allowed list yet):\n" + gated_list +
                "If the caller is asking to DO one of these (e.g. place or request an "
                "order, check their account, get their report) — as opposed to just "
                "asking a general how-to question — choose \"verification\", because they "
                "must verify before you can help. Only choose \"kb\" for genuine general "
                "information questions, not for requests to actually perform an action.\n"
                if gated_domains else ""
            )
            + "Also set connect_human=true if the caller is asking to speak to a human, "
            "agent, representative, or person; else false.\n"
            "Set small_talk=true ONLY when the message is purely social (greeting, thanks, apology, 'hold on', 'how are you') "
            "with NO actionable request. If there is ANY real request, small_talk is false.\n"
            "Set answer_from_context=true ONLY when the caller's question is ALREADY "
            "ANSWERED earlier in this conversation (a detail you already gave, an action "
            "you already did). Not for a fresh/live status check.\n"
            "Respond ONLY with JSON: "
            '{"domain": "...", "confidence": 0.0-1.0, "connect_human": false}'
        )
        # recent history gives cross-turn context for slot extraction
        msgs = [{"role": "system", "content": system}]
        for h in history[-HISTORY_WINDOW:]:
            msgs.append(h)
        msgs.append({"role": "user", "content": transcript})

        resp = self._complete(
            messages=msgs,
            response_format={"type": "json_object"},
            max_completion_tokens=200,
        )
        raw = resp.choices[0].message.content or "{}"
        logging.getLogger("heartbug").info(f"[{call_id}] ROUTER llm-raw: {raw}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return {
            "domain": data.get("domain", "unknown"),
            "confidence": float(data.get("confidence", 0.0)),
            "connect_human": bool(data.get("connect_human", False)),
            "small_talk": bool(data.get("small_talk", False)),
            "answer_from_context": bool(data.get("answer_from_context", False)),
        }
    
    # ── tool selection: within a domain, pick the tool + extract its args ──────
    def select_tool(self, call_id: str, transcript: str, candidate_tools: dict, history: list) -> dict:
        tool_block = ""
        for name, spec in candidate_tools.items():
            desc = spec.get("description", "")
            args = spec.get("args", {})
            arg_txt = (" (args: " + ", ".join(f"{a}: {h}" for a, h in args.items()) + ")") if args else " (no args)"
            tool_block += f"  - {name}: {desc}{arg_txt}\n"
        system = (
            "You are selecting the right tool to handle the caller's request within "
            "the chosen domain. Choose exactly one tool, and extract its arguments "
            "from the caller's message.\n"
            "Available tools:\n" + tool_block +
            "Extract only the arguments the chosen tool needs. If a required argument "
            "isn't present in the message, set it to null.\n"
            "When extracting a date of birth, output YYYY-MM-DD. Convert spoken/ordinal "
            "forms ('the fifteenth of March' -> 03-15; month names -> numbers) and spoken "
            "years (a two-digit year like 'eighty-five' is a STATED year -> infer a "
            "realistic birth-year century, so 'eighty-five' -> 1985, not 2085 or 0085). "
            "Only output a date when day, month, AND year are all present; else null. "
            "Never output the literal words 'First Last' for a name.\n"
            "Respond ONLY with JSON: "
            '{"tool": "...", "args": {...}, "confidence": 0.0-1.0}'
        )
        msgs = [{"role": "system", "content": system}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY_WINDOW:]]
        msgs.append({"role": "user", "content": transcript})
        resp = self._complete(
            messages=msgs,
            response_format={"type": "json_object"}, temperature=0)
        raw = resp.choices[0].message.content or "{}"
        logging.getLogger("heartbug").info(f"[{call_id}] TOOL-SELECT llm-raw: {raw}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return {
            "tool": data.get("tool"),
            "args": data.get("args", {}) or {},
            "confidence": float(data.get("confidence", 0.0)),
        }

    # ── planning: decompose a verified request into subtasks (may depend) ─────
    @tracing.traced("llm.plan", tracing.LLM)
    def plan(self, call_id: str, transcript: str, candidate_domains, history: list) -> dict:
        domain_block = "".join(f"  - {n}: {d}\n" for n, d in _domain_items(candidate_domains))
        system = (
            "You break a verified caller's request into subtasks, each handled by ONE "
            "domain from the allowed list. MOST requests are a SINGLE subtask — only "
            "split when the caller clearly asks about separate things.\n"
            "Allowed domains:\n" + domain_block +
            "Give each subtask an id (s1, s2, ...), its domain, and a short intent "
            "phrase for just that part.\n"
            "If a subtask NEEDS another subtask's result to run (e.g. you must find "
            "something before looking up details about it), list the ids it depends_on. "
            "Independent subtasks have depends_on: []. Decide dependencies from the "
            "request — never force one that isn't needed.\n"
            "Also set connect_human=true if they ask for a human/agent/person.\n"
            "Set small_talk=true ONLY when the message is purely social with NO "
            "actionable request — greetings, thanks, apologies, 'hold on a sec', "
            "'how are you'. If there is ANY real task, small_talk is false and you "
            "plan it normally.\n"
            "Set answer_from_context=true ONLY when the caller's question is ALREADY "
            "ANSWERED by something earlier in this conversation — e.g. they ask about "
            "an order/detail you already listed, or about an action you already did. "
            "In that case return NO subtasks; the answer will be given from the "
            "conversation. Do NOT use it for anything whose live status could have "
            "changed since (use a tool for a fresh status check).\n"
            "Respond ONLY with JSON: "
            '{"subtasks":[{"id":"s1","domain":"...","tool":"...","args":{},"depends_on":[]}],'
            '"confidence":0.0-1.0,"connect_human":false,"small_talk":false,"answer_from_context":false}'
        )
        msgs = [{"role": "system", "content": system}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY_WINDOW:]]
        msgs.append({"role": "user", "content": transcript})
        resp = self._complete(
            messages=msgs,
            response_format={"type": "json_object"}, temperature=0)
        raw = resp.choices[0].message.content or "{}"
        logging.getLogger("heartbug").info(f"[{call_id}] PLANNER llm-raw: {raw}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return {"subtasks": data.get("subtasks", []) or [],
                "confidence": float(data.get("confidence", 0.0)),
                "connect_human": bool(data.get("connect_human", False)),
                "small_talk": bool(data.get("small_talk", False)),
                "answer_from_context": bool(data.get("answer_from_context", False))}

    # ── Version B: ONE call — decompose + pick tool + extract args per subtask ──
    @tracing.traced("llm.plan_and_select", tracing.LLM)
    def plan_and_select(self, call_id: str, transcript: str, domain_tools: dict, history: list) -> dict:
        """
        Merged planner + tool-selection in a single call. domain_tools maps each
        allowed domain to its tools:
            {domain: {"description": str,
                      "tools": {tool: {"description": str, "args": {arg: hint}}}}}
        Returns subtasks already carrying their tool + args:
            {"subtasks": [{"id","domain","tool","args","depends_on":[ids]}],
             "confidence": float, "connect_human": bool, "small_talk": bool, "answer_from_context": bool}
        """
        block = ""
        for dom, spec in domain_tools.items():
            block += f"- domain '{dom}': {spec.get('description','')}\n"
            for tool, tspec in spec.get("tools", {}).items():
                args = tspec.get("args", {})
                arg_txt = (" args(" + ", ".join(f"{a}: {h}" for a, h in args.items()) + ")") if args else " (no args)"
                block += f"    tool '{tool}': {tspec.get('description','')}{arg_txt}\n"
        system = (
            "You are the orchestration brain of the HeartBug voice assistant. In ONE "
            "step: break the caller's request into subtasks, choose the domain AND the "
            "tool for each, and extract that tool's arguments. MOST requests are a "
            "single subtask.\n"
            "Available domains and their tools:\n" + block +
            "When building each subtask's query/intent, include relevant context from the whole caller message, "
            "not just the clause for that subtask. If the caller mentions a situation (travelling, going overseas, an upcoming event) "
            "that could affect the answer, carry that context into the query so retrieval sees it."
            "For each subtask give: id (s1, s2, ...), domain, tool (one of that domain's "
            "tools), args (extracted from the message; null if absent), and depends_on "
            "(ids of subtasks whose result it needs, else []).\n"
            "When extracting a date of birth output YYYY-MM-DD (convert spoken/ordinal "
            "forms and a stated two-digit year to a realistic birth year). Only split or "
            "chain when the request genuinely requires it.\n"
            "Also set connect_human=true if they ask for a human/agent/person.\n"
             "Set small_talk=true ONLY when the message is purely social with NO "
            "actionable request — greetings, thanks, apologies, 'hold on a sec', "
            "'how are you'. If there is ANY real task, small_talk is false and you "
            "plan it normally.\n"
            "Set answer_from_context=true ONLY when the caller's question is ALREADY "
            "ANSWERED by something earlier in this conversation — e.g. they ask about "
            "an order/detail you already listed, or about an action you already did. "
            "In that case return NO subtasks; the answer will be given from the "
            "conversation. Do NOT use it for anything whose live status could have "
            "changed since (use a tool for a fresh status check).\n"
            "Respond ONLY with JSON: "
            '{"subtasks":[{"id":"s1","domain":"...","tool":"...","args":{},"depends_on":[]}],'
            '"confidence":0.0-1.0,"connect_human":false,"small_talk":false,"answer_from_context":false}'
        )
        msgs = [{"role": "system", "content": system}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY_WINDOW:]]
        msgs.append({"role": "user", "content": transcript})
        resp = self._complete(
            messages=msgs,
            response_format={"type": "json_object"}, temperature=0)
        raw = resp.choices[0].message.content or "{}"
        logging.getLogger("heartbug").info(f"[{call_id}] PLAN+SELECT llm-raw: {raw}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return {"subtasks": data.get("subtasks", []) or [],
                "confidence": float(data.get("confidence", 0.0)),
                "connect_human": bool(data.get("connect_human", False)),
                "small_talk": bool(data.get("small_talk", False)),
                "answer_from_context": bool(data.get("answer_from_context", False))}
    
    # ── narration: LLM speaks one sentence from STRUCTURED FACTS ──────────────
    @tracing.traced("llm.narrate", tracing.LLM)
    def narrate(self, style: str, facts: dict, caller_said: str, history: list = None) -> str:
        # The caller's latest words are already the last entry in history, so we don't
        # re-quote them. We pass the recent windowed history as real chat turns, then
        # attach THIS turn's facts, and ask the model to reply to the latest turn.
        # Facts are the authoritative source for a fresh result; the short history lets
        # a follow-up about an already-stated detail be answered without a re-fetch.
        instruction = (
            "Reply to the caller's latest message in your warm HeartBug-agent voice, "
            "in one or two spoken sentences. Use these facts for this turn (never "
            f"invent beyond them): {json.dumps(facts)}\n"
            "If the caller is asking about a detail already stated earlier in this "
            "conversation, you may answer from that. Acknowledge their tone if they "
            "sound frustrated. Never invent anything that is in neither the facts nor "
            "the conversation. If you don't have what they asked for, say so plainly "
            "and move them forward — offer to help with something else on their "
            "HeartBug account, or to put them through to the team."
        )
        msgs = [{"role": "system", "content": style}]
        if history:
            msgs += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY_WINDOW:]]
        else:
            # no history supplied (e.g. greetings) → carry the caller's words directly
            msgs.append({"role": "user", "content": caller_said})
        msgs.append({"role": "user", "content": instruction})
        resp = self._complete(
            messages=msgs,
            max_completion_tokens=110,
        )
        return (resp.choices[0].message.content or "").strip()
    
    @tracing.traced("llm.answer_from_context", tracing.LLM)
    def answer_from_context(self, style: str, caller_said: str, history: list) -> str:
        """Answer a follow-up from the conversation so far (no tool). Two uses:
        (1) the detail was already stated (a date, carrier, status, item) — repeat it
        naturally; (2) the caller asserts something that contradicts what was listed —
        gently correct them from what was already said. Strictly grounded: use ONLY
        what the conversation contains; if it genuinely isn't there, say you'll look
        it up and offer to — never invent."""
        system = (
            style + "\n\nAnswer using ONLY what has already been said in this "
            "conversation. If the caller asks about a detail you already gave, restate "
            "it clearly. If they claim something that does not match what you told them "
            "(e.g. an item that was not in the orders you listed), gently correct them "
            "using what was actually said. Do NOT invent details; if the answer truly "
            "isn't in the conversation, say you'll look it up and offer to do that."
        )
        msgs = [{"role": "system", "content": system}]
        msgs += [{"role": m["role"], "content": m["content"]} for m in history[-HISTORY_WINDOW:]]
        msgs.append({"role": "user", "content": caller_said})
        resp = self._complete(
            messages=msgs,
            max_completion_tokens=130,
        )
        return (resp.choices[0].message.content or "").strip()
