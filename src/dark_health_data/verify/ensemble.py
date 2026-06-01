"""Heterogeneous-ensemble reconciliation (the "mixture of experts" verifier).

Run two (or more) *decorrelated* extractors -- e.g. a text LLM (Claude) and a vision
LLM (Qwen) -- over the same document, match their records on an identity key, and
compare fields. Agreement is a strong, label-free confidence signal; disagreement
localizes exactly the fields worth a human's time. Records the secondary extractor
found but the primary missed are surfaced as *omissions* (a recall check the
per-record verifiers cannot provide).

Ensembles only help when errors are uncorrelated, so pair different modalities, not
two prompts to the same model.
"""

from __future__ import annotations

from typing import Any, Callable

from .signals import Signal


def _agree(a: Any, b: Any, rel_tol: float) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        denom = max(abs(a), abs(b), 1e-9)
        return abs(a - b) / denom <= rel_tol
    return str(a).strip().lower() == str(b).strip().lower()


def reconcile_extractions(
    primary: list[Any],
    secondary: list[Any],
    *,
    key_fn: Callable[[Any], Any],
    fields: list[str],
    rel_tol: float = 0.01,
) -> tuple[dict[int, list[Signal]], list[Any]]:
    """Return per-primary-record ensemble signals and a list of likely omissions.

    ``fields`` are compared between matched records; ``key_fn`` defines record identity.
    """
    sec_by_key: dict[Any, Any] = {k: r for r in secondary if (k := key_fn(r)) is not None}
    matched_keys: set[Any] = set()
    signals: dict[int, list[Signal]] = {}

    for p in primary:
        k = key_fn(p)
        if k is None:
            continue  # record type excluded from ensemble reconciliation
        s = sec_by_key.get(k)
        if s is None:
            signals[id(p)] = [Signal("ensemble", score=0.5, ok=None,
                                     detail="no corroborating extraction from 2nd model")]
            continue
        matched_keys.add(k)
        pj, sj = p.model_dump(), s.model_dump()
        disagreements = [f for f in fields if not _agree(pj.get(f), sj.get(f), rel_tol)]
        n = len(fields)
        score = (n - len(disagreements)) / n if n else 1.0
        if disagreements:
            detail = "models disagree on: " + ", ".join(
                f"{f} ({pj.get(f)} vs {sj.get(f)})" for f in disagreements
            )
            signals[id(p)] = [Signal("ensemble", score=score, ok=None, detail=detail, weight=1.5)]
        else:
            signals[id(p)] = [Signal("ensemble", score=1.0, ok=True,
                                     detail="2nd model agrees on all fields", weight=1.5)]

    omissions = [r for k, r in sec_by_key.items() if k not in matched_keys]
    return signals, omissions
