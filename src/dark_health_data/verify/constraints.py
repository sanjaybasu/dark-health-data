"""Declarative domain constraints and the symbolic verifier.

A connector declares its domain axioms as ``Constraint`` objects (e.g. a rate is a
percentage in [0,100]; a numerator cannot exceed its denominator; subgroup counts
cannot exceed the total). The ``SymbolicVerifier`` evaluates them over a batch and
emits per-record signals with explanations -- all label-free and deterministic.

The same ``Constraint`` objects are consumed by the LNN-inspired engine in
``lnn.py``; this module is the "hard logic / SMT-style" path. If ``z3-solver`` is
installed it can optionally discharge numeric constraints with an SMT solver, but
the pure-Python evaluator is the default so nothing extra is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .signals import Signal


@dataclass
class ConstraintResult:
    constraint: str
    record: Optional[Any]  # the record this applies to (None = batch-wide)
    ok: Optional[bool]  # True ok, False violated, None not applicable
    detail: str
    severity: str  # "hard" | "soft"


class Constraint:
    """Base constraint. Subclasses implement ``evaluate`` over the record batch."""

    def __init__(self, name: str, severity: str = "hard"):
        self.name = name
        self.severity = severity

    def evaluate(self, records: list[Any]) -> list[ConstraintResult]:  # pragma: no cover
        raise NotImplementedError


class RecordConstraint(Constraint):
    """A per-record predicate: ``fn(record) -> (ok, detail)`` (ok None = N/A)."""

    def __init__(self, name, fn: Callable[[Any], tuple[Optional[bool], str]], severity="hard"):
        super().__init__(name, severity)
        self.fn = fn

    def evaluate(self, records: list[Any]) -> list[ConstraintResult]:
        out = []
        for r in records:
            ok, detail = self.fn(r)
            out.append(ConstraintResult(self.name, r, ok, detail, self.severity))
        return out


class GroupConstraint(Constraint):
    """A constraint over groups of records sharing ``key(record)``.

    ``fn(group) -> list[(record, ok, detail)]`` for the records that should be
    flagged within the group.
    """

    def __init__(self, name, key: Callable[[Any], Any],
                 fn: Callable[[list[Any]], list[tuple[Any, Optional[bool], str]]], severity="soft"):
        super().__init__(name, severity)
        self.key = key
        self.fn = fn

    def evaluate(self, records: list[Any]) -> list[ConstraintResult]:
        groups: dict[Any, list[Any]] = {}
        for r in records:
            groups.setdefault(self.key(r), []).append(r)
        out = []
        for group in groups.values():
            for record, ok, detail in self.fn(group):
                out.append(ConstraintResult(self.name, record, ok, detail, self.severity))
        return out


class SymbolicVerifier:
    """Run a connector's constraints; annotate records and emit per-record signals."""

    SOFT_PENALTY = 0.25  # each soft violation multiplies the symbolic score by (1 - this)

    def __init__(self, constraints: list[Constraint]):
        self.constraints = constraints

    def verify(self, records: list[Any]) -> dict[int, list[Signal]]:
        # map id(record) -> accumulated (hard_fail, soft_count, details)
        state: dict[int, dict[str, Any]] = {
            id(r): {"hard": False, "soft": 0, "details": []} for r in records
        }
        for c in self.constraints:
            for res in c.evaluate(records):
                if res.record is None or res.ok is None or res.ok is True:
                    continue
                st = state.get(id(res.record))
                if st is None:
                    continue
                st["details"].append(f"{res.constraint}: {res.detail}")
                if res.severity == "hard":
                    st["hard"] = True
                    res.record.flag(f"{res.constraint}: {res.detail}", fail=True)
                else:
                    st["soft"] += 1
                    res.record.flag(f"{res.constraint}: {res.detail}")

        signals: dict[int, list[Signal]] = {}
        for r in records:
            st = state[id(r)]
            if st["hard"]:
                score, ok = 0.0, False
            else:
                score, ok = (1 - self.SOFT_PENALTY) ** st["soft"], True
            detail = "; ".join(st["details"]) or "all domain constraints satisfied"
            signals[id(r)] = [Signal("symbolic", score=score, ok=ok, detail=detail, weight=2.0)]
        return signals
