"""Verification layer: defense-in-depth checks that make extracted data trustworthy.

Pipeline of complementary, mostly label-free verifiers:

  symbolic  -- declarative domain axioms (num<=den, rate in range, ...)        [Layer 2a]
  lnn       -- LNN-inspired bounded-logic contradiction detection              [Layer 2a]
  grounding -- cited source span really contains the value                     [Layer 1]
  ensemble  -- a 2nd, decorrelated extractor agrees (optional)                 [Layer 2c]

Signals are fused into a per-record ``trust_score`` (label-free), and a review gate
(simple threshold, or a calibrated ``ConformalGate``) sets ``review_recommended``.

See ``docs/verification.md`` for the full SOTA design and where each piece sits.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..models import QAStatus
from .conformal import ConformalGate
from .constraints import (
    Constraint,
    GroupConstraint,
    RecordConstraint,
    SymbolicVerifier,
)
from .ensemble import reconcile_extractions
from .grounding import GroundingVerifier
from .lnn import Interval, LNNVerifier, conj, disj, implies, neg
from .signals import Signal, TrustModel

__all__ = [
    "verify_records", "Constraint", "RecordConstraint", "GroupConstraint",
    "SymbolicVerifier", "LNNVerifier", "GroundingVerifier", "ConformalGate",
    "reconcile_extractions", "Signal", "TrustModel",
    "Interval", "conj", "disj", "implies", "neg",
]


def verify_records(
    records: list[Any],
    *,
    connector: Any,
    doc_texts: Optional[dict[str, str]] = None,
    second_records: Optional[list[Any]] = None,
    ensemble_key_fn: Optional[Callable[[Any], Any]] = None,
    ensemble_fields: Optional[list[str]] = None,
    gate: Optional[ConformalGate] = None,
    trust_model: Optional[TrustModel] = None,
    review_threshold: float = 0.85,
) -> dict[str, Any]:
    """Run the verification suite, annotate records (trust_score, review, flags), and
    return a summary. Label-free verifiers always run; ensemble runs if ``second_records``
    is given; the conformal ``gate`` is used for the review decision if provided."""
    constraints: list[Constraint] = list(connector.constraints())
    tm = trust_model or TrustModel()

    sym = SymbolicVerifier(constraints).verify(records)
    lnn = LNNVerifier(constraints).verify(records)
    ground = GroundingVerifier().verify(records, doc_texts or {})

    ens: dict[int, list[Signal]] = {}
    omissions: list[Any] = []
    if second_records is not None and ensemble_key_fn is not None:
        ens, omissions = reconcile_extractions(
            records, second_records, key_fn=ensemble_key_fn, fields=ensemble_fields or []
        )

    for r in records:
        signals = sym.get(id(r), []) + lnn.get(id(r), []) + ground.get(id(r), []) + ens.get(id(r), [])
        trust, _rationale = tm.fuse(signals)
        r.trust_score = trust
        # surface new failure evidence (grounding/ensemble) into qa_flags; symbolic
        # already flags its own violations, and lnn mirrors symbolic, so skip those.
        for s in signals:
            if s.name == "grounding" and s.ok is False:
                r.flag(f"grounding: {s.detail}", fail=True)
            elif s.name == "ensemble" and s.ok is None and "disagree" in s.detail:
                r.flag(s.detail)

    if gate is not None:
        gate_summary = gate.annotate(records)
    else:
        for r in records:
            r.review_recommended = (r.qa_status == QAStatus.FAIL) or (
                r.trust_score is not None and r.trust_score < review_threshold
            )
        gate_summary = {"method": "threshold", "review_threshold": review_threshold}

    from collections import Counter

    status_counts = Counter(r.qa_status.value for r in records)
    trusts = [r.trust_score for r in records if r.trust_score is not None]
    n_review = sum(1 for r in records if r.review_recommended)
    return {
        "n_records": len(records),
        "qa_pass": status_counts.get(QAStatus.PASS.value, 0),
        "qa_warn": status_counts.get(QAStatus.WARN.value, 0),
        "qa_fail": status_counts.get(QAStatus.FAIL.value, 0),
        "mean_trust": round(sum(trusts) / len(trusts), 4) if trusts else None,
        "min_trust": round(min(trusts), 4) if trusts else None,
        "review_recommended": n_review,
        "auto_acceptable": len(records) - n_review,
        "ensemble_omissions": len(omissions),
        "gate": gate_summary,
    }
