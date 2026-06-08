"""Extractor selection."""

from __future__ import annotations

from .base import Extractor, RuleExtractor


def get_extractor(name: str, **kwargs) -> Extractor:
    """Return an extractor by name: 'rule' (offline), 'llm' (Claude, synchronous),
    'llm_batch' (Claude via the Message Batches API; ~50% cheaper, async bulk -- use
    ``dhd batch``), or 'vlm' (any OpenAI-compatible/local model, e.g. Qwen)."""
    if name == "rule":
        return RuleExtractor()
    if name == "llm":
        from .llm import LLMExtractor  # lazy: optional dependency

        return LLMExtractor(**kwargs)
    if name == "llm_batch":
        from .llm import BatchLLMExtractor  # lazy: optional dependency

        return BatchLLMExtractor(**kwargs)
    if name in ("vlm", "openai"):
        from .openai_compatible import OpenAICompatExtractor  # lazy: optional dependency

        return OpenAICompatExtractor(**kwargs)
    raise ValueError(f"Unknown extractor '{name}'. Use 'rule', 'llm', 'llm_batch', or 'vlm'.")


__all__ = ["Extractor", "RuleExtractor", "get_extractor"]
