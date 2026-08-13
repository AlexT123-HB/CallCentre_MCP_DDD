"""Host router — classify a caller turn into domain + entities via the LLM provider.

candidate_domains is a {name: description} map (from manifests) so the LLM prompt
can describe what each domain handles, generated from manifests — not hardcoded.
"""

from __future__ import annotations

from src.client.providers.interfaces import LLMProvider


class Router:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def classify_turn(self, call_id: str, transcript: str, candidate_domains, history: list, gated_domains: None) -> dict:
        """candidate_domains: {name: description}. gated_domains: {name: description}
        of domains that require verification the caller doesn't have yet — used to
        steer an unverified caller to verify first instead of mis-routing their
        intent to a public domain. Returns {domain, confidence, entities}."""
        return self._provider.classify(call_id, transcript, candidate_domains, history, gated_domains=gated_domains)
