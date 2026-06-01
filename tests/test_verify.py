"""Tests for the verification layer: bounded logic, symbolic + LNN, grounding,
ensemble reconciliation, and the conformal gate."""

from dark_health_data.connectors.eqr import EQRConnector
from dark_health_data.models import (
    EQRQualityMeasure,
    ExtractionMethod,
    Provenance,
    QAStatus,
)
from dark_health_data.verify import (
    ConformalGate,
    GroundingVerifier,
    Interval,
    LNNVerifier,
    SymbolicVerifier,
    conj,
    implies,
    neg,
    reconcile_extractions,
)


def _measure(**kw):
    base = dict(
        provenance=Provenance(source_document_id="doc1", method=ExtractionMethod.LLM),
        state="TX", plan_name="Plan A", measure_name="CBP", reporting_year=2024,
    )
    base.update(kw)
    return EQRQualityMeasure(**base)


# --- bounded logic ---
def test_bounded_logic_operators_and_contradiction():
    assert neg(Interval.known(1.0)) == Interval.known(0.0)
    # AND of a true and a false fact cannot be true
    v = conj([Interval.known(1.0), Interval.known(0.0)])
    assert v.upper == 0.0
    asserted = Interval(max(v.lower, 1.0), v.upper)  # assert it must hold
    assert asserted.is_contradiction
    # implication truth
    assert implies(Interval.known(1.0), Interval.known(1.0)).lower == 1.0


# --- symbolic + LNN ---
def test_symbolic_and_lnn_flag_impossible_measure():
    bad = _measure(rate=49.3, numerator=2100, denominator=2000)  # num > den
    cons = EQRConnector().constraints()

    sym = SymbolicVerifier(cons).verify([bad])
    assert sym[id(bad)][0].ok is False
    assert bad.qa_status == QAStatus.FAIL

    lnn = LNNVerifier(cons).verify([bad])
    sig = lnn[id(bad)][0]
    assert sig.ok is False and sig.score == 0.0
    assert "num_le_den" in sig.detail


def test_symbolic_passes_clean_measure():
    good = _measure(rate=61.7, numerator=617, denominator=1000)
    cons = EQRConnector().constraints()
    sym = SymbolicVerifier(cons).verify([good])
    assert sym[id(good)][0].ok is True
    assert good.qa_status == QAStatus.PASS


# --- grounding ---
def test_grounding_detects_unsupported_value():
    grounded = _measure(rate=58.2, numerator=1164, denominator=2000)
    grounded.provenance.source_span = "CBP rate 58.2% (1,164 / 2,000)"
    hallucinated = _measure(rate=58.2, numerator=1164, denominator=2000)
    hallucinated.provenance.source_span = "the plan performed well overall"

    doc_texts = {"doc1": "... CBP rate 58.2% (1,164 / 2,000) ... the plan performed well overall ..."}
    sigs = GroundingVerifier().verify([grounded, hallucinated], doc_texts)
    assert sigs[id(grounded)][0].ok is True
    assert sigs[id(hallucinated)][0].ok is False


# --- ensemble ---
def test_ensemble_reconcile_agreement_disagreement_omission():
    def key(r):
        return (r.state, r.plan_name, r.measure_name, r.reporting_year)

    p1 = _measure(rate=58.2)
    p2 = _measure(measure_name="W30", rate=70.0)
    s1 = _measure(rate=58.2)  # agrees with p1
    s2 = _measure(measure_name="W30", rate=51.0)  # disagrees with p2 on rate
    s3 = _measure(measure_name="BCS", rate=49.0)  # present only in secondary -> omission

    signals, omissions = reconcile_extractions(
        [p1, p2], [s1, s2, s3], key_fn=key, fields=["rate"]
    )
    assert signals[id(p1)][0].ok is True
    assert "disagree" in signals[id(p2)][0].detail
    assert len(omissions) == 1 and omissions[0].measure_name == "BCS"


# --- conformal gate ---
def test_conformal_gate_bounds_accepted_error():
    calib = [(0.9, True, "x")] * 40 + [(0.3, False, "x")] * 20
    gate = ConformalGate(alpha=0.2, delta=0.1).calibrate(calib)
    assert gate.global_threshold <= 0.9  # high-score region is acceptable
    assert gate.decide(0.95) == "accept"
    assert gate.decide(0.3) == "review"

    records = [_measure(rate=61.7, numerator=617, denominator=1000) for _ in range(3)]
    for r, t in zip(records, [0.95, 0.95, 0.3]):
        r.trust_score = t
    summary = gate.annotate(records)
    assert summary["auto_accepted"] == 2 and summary["sent_to_review"] == 1
