"""
Provider factory — returns the configured LLMProvider.

The host calls get_provider() and gets a real LLM-backed provider. All reasoning
is done by the LLM; there is no rule-based fallback. If no API key is configured,
this raises a clear error rather than silently degrading.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

from src.client.providers.interfaces import LLMProvider

load_dotenv()

def get_provider() -> LLMProvider:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. The host uses an LLM for all routing and "
            "narration. Set OPENAI_API_KEY (and optionally HEARTBUG_MODEL) to run."
        )
    from src.client.providers.openai_provider import OpenAIProvider
    return OpenAIProvider()
