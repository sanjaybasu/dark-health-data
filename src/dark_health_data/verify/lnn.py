"""LNN-inspired bounded-logic verifier.

This is a compact, dependency-free engine in the spirit of Logical Neural Networks
(Riegel et al., 2020): truth values are *intervals* ``[lower, upper]`` over [0,1],
connectives use real-valued (Lukasiewicz) logic, and asserting that a proposition
must hold tightens its lower bound -- so a **contradiction** appears exactly when a
required proposition's asserted lower bound exceeds the upper bound implied by the
data. That gives us explainable, per-axiom error attribution with zero labels.

What this is NOT: the full IBM LNN with learnable weights and FOL quantifiers. The
domain axioms here are grounded per record. ``LNNVerifier`` is built to consume the
same ``Constraint`` objects a connector already declares, and the ``Interval`` algebra
below is a drop-in target if you later swap in the `lnn` package for weight learning.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constraints import Constraint
from .signals import Signal

EPS = 1e-9


@dataclass(frozen=True)
class Interval:
    lower: float
    upper: float

    @property
    def is_contradiction(self) -> bool:
        return self.lower > self.upper + EPS

    @staticmethod
    def known(truth: float) -> "Interval":
        return Interval(truth, truth)


# truth interval representing "unknown / not applicable"
Interval.UNKNOWN = Interval(0.0, 1.0)


# --- Lukasiewicz real-valued logic over truth intervals (monotone, so bounds map cleanly) ---
def neg(a: Interval) -> Interval:
    return Interval(1.0 - a.upper, 1.0 - a.lower)


def conj(intervals: list[Interval]) -> Interval:
    """N-ary AND: T(x1..xn) = max(0, sum(xi) - (n-1))."""
    if not intervals:
        return Interval.known(1.0)  # vacuous truth
    n = len(intervals)
    lower = max(0.0, sum(i.lower for i in intervals) - (n - 1))
    upper = max(0.0, sum(i.upper for i in intervals) - (n - 1))
    return Interval(lower, upper)


def disj(intervals: list[Interval]) -> Interval:
    """N-ary OR: S(x1..xn) = min(1, sum(xi))."""
    if not intervals:
        return Interval.known(0.0)
    return Interval(min(1.0, sum(i.lower for i in intervals)),
                    min(1.0, sum(i.upper for i in intervals)))


def implies(a: Interval, b: Interval) -> Interval:
    """Lukasiewicz implication I(a,b) = min(1, 1 - a + b)."""
    return Interval(min(1.0, 1.0 - a.upper + b.lower), min(1.0, 1.0 - a.lower + b.upper))


def assert_true(a: Interval, lower: float = 1.0) -> Interval:
    """Assert a proposition holds by raising its lower bound (LNN downward pass)."""
    return Interval(max(a.lower, lower), a.upper)


class LNNVerifier:
    """Per-record logical validity via bounded-logic contradiction detection."""

    def __init__(self, constraints: list[Constraint]):
        # only hard constraints participate in the logical-validity proposition
        self.constraints = [c for c in constraints if c.severity == "hard"]

    def verify(self, records: list) -> dict[int, list[Signal]]:
        # collect each hard constraint's outcome per record
        per_record: dict[int, list[tuple[str, Interval]]] = {id(r): [] for r in records}
        for c in self.constraints:
            for res in c.evaluate(records):
                if res.record is None:
                    continue
                if res.ok is True:
                    atom = Interval.known(1.0)
                elif res.ok is False:
                    atom = Interval.known(0.0)
                else:
                    atom = Interval.UNKNOWN  # not applicable -> no logical force
                per_record[id(res.record)].append((res.constraint, atom))

        signals: dict[int, list[Signal]] = {}
        for r in records:
            atoms = per_record[id(r)]
            validity = conj([a for _, a in atoms])
            asserted = assert_true(validity, 1.0)  # "this record must be valid"
            culprits = [name for name, a in atoms if a.upper < 1.0 - EPS]
            if asserted.is_contradiction:
                detail = "logical contradiction; violated axiom(s): " + ", ".join(culprits)
                sig = Signal("lnn", score=validity.upper, ok=False, detail=detail, weight=2.0)
            else:
                sig = Signal("lnn", score=validity.upper, ok=True,
                             detail="no logical contradiction", weight=1.0)
            signals[id(r)] = [sig]
        return signals
