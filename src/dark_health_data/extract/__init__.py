"""Extractor selection."""

from __future__ import annotations

from .base import Extractor, RuleExtractor


def get_extractor(name: str, **kwargs) -> Extractor:
    """Return an extractor by name: 'rule' (offline), 'llm' (Claude), or
    'vlm' (any OpenAI-compatible/local model, e.g. Qwen via Ollama or vLLM)."""
    if name == "rule":
        return RuleExtractor()
    if name == "llm":
        from .llm import LLMExtractor  # lazy: optional dependency

        return LLMExtractor(**kwargs)
    if name in ("vlm", "openai"):
        from .openai_compatible import OpenAICompatExtractor  # lazy: optional dependency

        return OpenAICompatExtractor(**kwargs)
    raise ValueError(f"Unknown extractor '{name}'. Use 'rule', 'llm', or 'vlm'.")


__all__ = ["Extractor", "RuleExtractor", "get_extractor"]
