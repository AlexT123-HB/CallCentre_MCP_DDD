"""
LLMProvider interface — the host depends on this, not on any vendor.

All reasoning (intent classification, entity extraction, narration) is done by an
LLM through this interface. Swap providers (OpenAI, Anthropic, Azure) without
touching the host. No rule-based logic — the LLM does the work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class LLMProvider(ABC):
    @abstractmethod
    def classify_confirmation(self, call_id: str, transcript: str, prompt: str, history: list,
                              answers: dict) -> str:
        """Return a declared answer label (from `answers`) or 'unclear'."""
        raise NotImplementedError

    def classify(self, call_id: str, transcript: str, candidate_domains: List[str], history: list = None, gated_domains=None) -> dict:
        """
        Classify a caller turn. Returns:
            {"domain": str, "confidence": float, "entities": {"name":..., "dob":...}}
        domain must be one of candidate_domains (or "unknown").
        """
        ...

    @abstractmethod
    def select_tool(self, call_id: str, transcript: str, candidate_tools: dict, history: list) -> dict:
        """
        Within an already-chosen domain, pick which tool handles the turn and extract
        its business arguments. candidate_tools is {tool_name: {"description": str,
        "args": {arg_name: hint}}} (identity params already filtered out by the host).
        Returns: {"tool": str, "args": {...}, "confidence": float}.
        """
        ...

    @abstractmethod
    def narrate(self, style: str, facts: dict, caller_said: str, history: list = None) -> str:
        """Produce ONE spoken sentence from structured facts. No raw history."""
        ...
    def answer_from_context(self, style: str, caller_said: str, history: list) -> str:
        """Answer a follow-up using ONLY the conversation so far — no tool call.
        `style` is the base narration voice (the Narrator passes it in)."""
        ...

    def plan(self, call_id: str, transcript: str, candidate_domains, history: list) -> dict:
        """
        Decompose a verified caller's request into subtasks, each mapped to an
        ALLOWED domain. The LLM decides at runtime which domains AND whether any
        subtask depends on another's result (depends_on lists earlier ids). Most
        requests are a single subtask.
        Returns: {"subtasks": [{"id","domain","intent","depends_on":[ids]}],
                  "confidence": float, "connect_human": bool}.
        Default implementation falls back to single-domain classify (so providers
        that don't override still work).
        """
        c = self.classify(call_id, transcript, candidate_domains, history)
        return {"subtasks": [{"id": "s1", "domain": c.get("domain", "unknown"),
                              "intent": transcript, "depends_on": []}],
                "confidence": c.get("confidence", 0.0),
                "connect_human": c.get("connect_human", False)}

    def plan_and_select(self, call_id: str, transcript: str, domain_tools: dict, history: list) -> dict:
        """Version B: merged decompose + tool-select + arg-extract in one call.
        domain_tools maps domain → {description, tools:{tool:{description,args}}}.
        Returns subtasks with tool+args already filled. Providers that don't override
        get a NotImplementedError (Version B requires a real LLM)."""
        raise NotImplementedError("plan_and_select requires an LLM-backed provider")

