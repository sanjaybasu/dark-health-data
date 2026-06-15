#!/usr/bin/env python3
"""Reproduce the validation calibration analysis reported in the Data Resource Profile.

Joins the human labels (reviewers' `correct` column, by row_uid) to each record's fused
`trust_score` (from the drawn gold sample), then:
  * reports field accuracy and the risk-coverage behaviour of the trust score, and
  * runs the conformal gate (verify.conformal.ConformalGate) at target accepted-error
    rates, showing whether a low-error acceptance set can be certified on this sample.

The headline finding it documents: on this labelled set the trust score is near-degenerate
and does not rank-separate the residual (value-level) errors, so the gate cannot certify
alpha = 0.05 here. Inputs live under private/ (PHI-free, public-record derived) and are not
committed; pass paths if yours differ.

Usage:
  python scripts/conformal_eval.py [--gold gold/eqr_sample.csv]
      [--reviewer-a <xlsx>] [--reviewer-b <xlsx>] [--alpha 0.05 0.10] [--delta 0.05]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dark_health_data.verify.conformal import _error_ucb, _threshold  # noqa: E402


def _labels(path: str) -> dict[str, int]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Review"]
    rows = list(ws.iter_rows(values_only=True))
    H = {str(h): i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        u, v = r[H["row_uid"]], r[H["correct"]]
        if u is not None and v in (0, 1):
            out[str(u)] = int(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold/eqr_sample.csv")
    ap.add_argument("--reviewer-a", default="private/review-packet/eqr_validation_reviewerA_basu.FILLED.xlsx")
    ap.add_argument("--reviewer-b", required=True, help="completed reviewer-B (Berkowitz) sheet")
    ap.add_argument("--alpha", type=float, nargs="+", default=[0.05, 0.10])
    ap.add_argument("--delta", type=float, default=0.05)
    args = ap.parse_args()

    gold = {}
    for r in csv.DictReader(open(args.gold)):
        try:
            gold[r["row_uid"]] = float(r["trust_score"])
        except (KeyError, ValueError, TypeError):
            pass

    A, B = _labels(args.reviewer_a), _labels(args.reviewer_b)
    labels = dict(A)
    for u, v in B.items():  # consensus on overlap: correct only if both reviewers say correct
        labels[u] = (1 if labels.get(u, 1) == 1 and v == 1 else 0) if u in labels else v

    pairs = [(gold[u], bool(c)) for u, c in labels.items() if u in gold]
    n = len(pairs)
    err = sum(1 for _, c in pairs if not c)
    print(f"labelled rows joined to trust_score: {n} | accept-all accuracy: {(n-err)/n:.3f} ({err} errors)")

    distinct = sorted({round(s, 4) for s, _ in pairs}, reverse=True)
    print(f"distinct trust values on the labelled set: {distinct}")
    err_at_max = sum(1 for s, c in pairs if not c and round(s, 4) == distinct[0])
    print(f"errors carrying the MAX trust score: {err_at_max}/{err}")

    for alpha in args.alpha:
        tau = _threshold([(s, c) for s, c in pairs], alpha, args.delta)
        acc = [(s, c) for s, c in pairs if s >= tau]
        e = sum(1 for _, c in acc if not c)
        ucb = _error_ucb(len(acc), e, args.delta) if acc else 1.0
        cov = len(acc) / n if n else 0.0
        certified = tau != float("inf")
        print(f"  alpha={alpha}: {'tau=%.4f' % tau if certified else 'NOT CERTIFIABLE (tau=inf)'} "
              f"| coverage={cov:.3f} | accepted-error UCB={ucb:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
