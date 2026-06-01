"""Tests for the gold-set evaluation + conformal calibration workflow."""

import csv

from dark_health_data.evaluation import (
    evaluate,
    risk_coverage,
    row_uid,
    stratified_sample,
    wilson_ci,
    write_sample,
)
from dark_health_data.pipeline import run_dataset


def test_wilson_ci_bounds():
    lo, hi = wilson_ci(9, 10)
    assert 0.0 <= lo < 0.9 < hi <= 1.0
    assert wilson_ci(0, 0) == [0.0, 1.0]


def test_row_uid_stable_and_identity_based():
    a = {"state": "TX", "plan_name": "Superior", "x": 1}
    b = {"state": "tx", "plan_name": " superior ", "x": 999}  # differs only on non-id / case / ws
    cols = ["state", "plan_name"]
    assert row_uid(a, cols) == row_uid(b, cols)


def test_stratified_sample_respects_size_and_strata():
    rows = [{"g": "a"}] * 30 + [{"g": "b"}] * 10
    s = stratified_sample(rows, 20, stratify_key="g", seed=1)
    assert 15 <= len(s) <= 22  # ~proportional, both strata represented
    assert any(r["g"] == "b" for r in s)


def test_risk_coverage_is_sorted_by_trust():
    curve = risk_coverage([(0.9, True), (0.2, False), (0.5, True)])
    assert len(curve) == 3
    assert curve[0]["coverage"] < curve[-1]["coverage"]
    assert curve[0]["trust_threshold"] >= curve[-1]["trust_threshold"]


def test_sample_and_evaluate_end_to_end(tmp_path):
    run_dataset("eqr", extractor_name="rule", verbose=False, write_parquet=False)

    gold = tmp_path / "gold.csv"
    info = write_sample("eqr", n=13, out_path=gold)
    assert info["sampled"] == 13
    assert gold.exists()

    # simulate a human: mark a row correct iff its trust_score cleared 0.5
    with gold.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = rows[0].keys()
    for r in rows:
        r["correct"] = "1" if float(r["trust_score"]) >= 0.5 else "0"
    with gold.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fieldnames))
        w.writeheader()
        w.writerows(rows)

    report = evaluate("eqr", gold, alpha=0.5, delta=0.3)
    assert report["n_labeled"] == 13
    assert report["n_unmatched_gold"] == 0
    assert report["overall_accuracy"] == round(12 / 13, 4)  # only the impossible measure is "wrong"
    assert len(report["risk_coverage"]) == 13
    assert report["conformal_threshold"] is not None  # a finite threshold exists at this alpha
    ci = report["overall_accuracy_ci95"]
    assert ci[0] <= report["overall_accuracy"] <= ci[1] and 0.0 <= ci[0] <= ci[1] <= 1.0
