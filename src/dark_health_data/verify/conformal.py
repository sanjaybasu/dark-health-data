"""Conformal-style selective acceptance.

Turn trust scores into a defensible decision: auto-accept records whose trust is high
enough that the error rate among accepted is provably bounded, and route the rest to
human review. Given a small labeled calibration set, ``ConformalGate`` picks the
acceptance threshold with the *largest* coverage whose finite-sample upper confidence
bound on the accepted error rate stays <= ``alpha``.

Stratified ("Mondrian") mode fits a separate threshold per group (e.g. per state or
per measure), which is what you want here because exchangeability breaks across
states/years -- a single global threshold would silently lose its guarantee under
that distribution shift.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

_INF = float("inf")


def _error_ucb(n: int, errors: int, delta: float) -> float:
    """Hoeffding upper confidence bound on the error rate (distribution-free)."""
    if n == 0:
        return 1.0
    phat = errors / n
    return min(1.0, phat + math.sqrt(math.log(1.0 / delta) / (2 * n)))


def _threshold(pairs: list[tuple[float, bool]], alpha: float, delta: float) -> float:
    """Lowest score threshold whose accepted-set error UCB <= alpha (max coverage)."""
    if not pairs:
        return _INF
    candidates = sorted({s for s, _ in pairs}, reverse=True)
    best = _INF
    for tau in candidates:
        accepted = [correct for s, correct in pairs if s >= tau]
        n = len(accepted)
        errors = sum(1 for c in accepted if not c)
        if _error_ucb(n, errors, delta) <= alpha:
            best = tau  # keep lowering tau to accept more, as long as the bound holds
    return best


class ConformalGate:
    """Selective-acceptance gate calibrated on (score, correct) labels."""

    def __init__(self, alpha: float = 0.1, delta: float = 0.05,
                 group_fn: Optional[Callable[[Any], Any]] = None):
        self.alpha = alpha
        self.delta = delta
        self.group_fn = group_fn
        self.global_threshold: float = _INF
        self.group_thresholds: dict[Any, float] = {}

    def calibrate(self, calib: list[tuple[float, bool, Any]]) -> "ConformalGate":
        """``calib``: list of (trust_score, is_correct, group). group ignored if no group_fn."""
        self.global_threshold = _threshold([(s, c) for s, c, _ in calib], self.alpha, self.delta)
        if self.group_fn is not None:
            groups: dict[Any, list[tuple[float, bool]]] = {}
            for s, c, g in calib:
                groups.setdefault(g, []).append((s, c))
            self.group_thresholds = {
                g: _threshold(pairs, self.alpha, self.delta) for g, pairs in groups.items()
            }
        return self

    def threshold_for(self, record: Any) -> float:
        if self.group_fn is not None:
            g = self.group_fn(record)
            return self.group_thresholds.get(g, self.global_threshold)
        return self.global_threshold

    def decide(self, score: Optional[float], record: Any = None) -> str:
        if score is None:
            return "review"
        return "accept" if score >= self.threshold_for(record) else "review"

    def annotate(self, records: list[Any]) -> dict[str, Any]:
        """Set ``review_recommended`` per record; return a coverage summary."""
        accepted = 0
        for r in records:
            decision = self.decide(getattr(r, "trust_score", None), r)
            r.review_recommended = decision == "review"
            accepted += decision == "accept"
        n = len(records)
        return {
            "alpha": self.alpha,
            "delta": self.delta,
            "n": n,
            "auto_accepted": accepted,
            "sent_to_review": n - accepted,
            "coverage": round(accepted / n, 4) if n else 0.0,
            "stratified": self.group_fn is not None,
        }
