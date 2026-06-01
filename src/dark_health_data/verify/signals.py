"""Verification signals and their fusion into a single trust score.

Each verifier emits ``Signal`` objects. A signal carries an optional reliability
score in [0,1] and/or a hard pass/fail. The ``TrustModel`` fuses the signals for a
record into one ``trust_score`` and a human-readable rationale.

Design goals: label-free by default (so it runs with zero ground truth), explainable
(every score traces to named signals), and monotone (a hard logical failure forces a
low trust score). A learnable fusion can be layered on later from a gold set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    """One piece of verification evidence about a record."""

    name: str  # e.g. "symbolic", "grounding", "ensemble", "lnn", "self_consistency"
    score: Optional[float] = None  # reliability contribution in [0,1], or None if N/A
    ok: Optional[bool] = None  # hard pass/fail, or None if not a hard check
    detail: str = ""
    weight: float = 1.0


@dataclass
class TrustModel:
    """Fuse signals into a trust score in [0,1].

    Default (label-free) rule:
      * any hard failure (ok is False) clamps trust to <= ``hard_fail_ceiling``;
      * otherwise trust is the weighted geometric mean of available scores.
    Geometric mean (vs arithmetic) means one very-low signal drags the score down,
    which is the behavior we want for "any strong doubt => review".
    """

    hard_fail_ceiling: float = 0.05
    default_score: float = 0.8  # prior when a record has no informative signals

    def fuse(self, signals: list[Signal]) -> tuple[float, list[str]]:
        rationale: list[str] = []
        hard_fail = False
        weighted_log_sum = 0.0
        weight_sum = 0.0

        for s in signals:
            if s.ok is False:
                hard_fail = True
                rationale.append(f"FAIL[{s.name}]: {s.detail}")
            elif s.detail:
                tag = "ok" if s.ok else "info"
                rationale.append(f"{tag}[{s.name}]: {s.detail}")
            if s.score is not None:
                score = min(max(s.score, 1e-6), 1.0)
                weighted_log_sum += s.weight * _log(score)
                weight_sum += s.weight

        if weight_sum > 0:
            trust = _exp(weighted_log_sum / weight_sum)
        else:
            trust = self.default_score

        if hard_fail:
            trust = min(trust, self.hard_fail_ceiling)
        return round(trust, 4), rationale


# tiny stdlib math wrappers (avoid importing math at call sites / keep deps minimal)
def _log(x: float) -> float:
    import math

    return math.log(x)


def _exp(x: float) -> float:
    import math

    return math.exp(x)
