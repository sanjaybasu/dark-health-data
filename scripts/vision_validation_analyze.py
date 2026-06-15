#!/usr/bin/env python3
"""Analyse the returned vision-validation scores (run after both reviewers fill their sheets).

Reports, mirroring the text validation:
  * vision field accuracy (reviewer B / Seth, all rows) with a 95% Wilson CI;
  * inter-rater reliability on the double-scored overlap (Po, Cohen's kappa, Gwet's AC1);
  * vision accuracy broken down by text-agreement (from the unblinded KEY): corroborated by
    a text rate vs. plausibly graphical-only — the comprehensive vision x text picture.

Usage:
  python scripts/vision_validation_analyze.py \
      --reviewer-b <berkowitz_filled.xlsx> [--reviewer-a <basu_filled.xlsx>]
      [--key private/review-packet/eqr_vision_validation_KEY.csv]
"""
from __future__ import annotations

import argparse
import csv
import math

import openpyxl


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z / d * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return p, c - h, c + h


def load(path: str) -> dict[str, int]:
    ws = openpyxl.load_workbook(path, data_only=True)["Review"]
    rows = list(ws.iter_rows(values_only=True))
    H = {str(h): i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        u, v = r[H["row_uid"]], r[H["correct"]]
        try:  # cells may be typed as int, float, or text ("1"/"0") depending on the editor
            iv = int(str(v).strip())
        except (TypeError, ValueError):
            continue
        if u is not None and iv in (0, 1):
            out[str(u)] = iv
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer-b", required=True)
    ap.add_argument("--reviewer-a", default=None)
    ap.add_argument("--key", default="private/review-packet/eqr_vision_validation_KEY.csv")
    args = ap.parse_args()

    B = load(args.reviewer_b)
    k, n = sum(B.values()), len(B)
    p, lo, hi = wilson(k, n)
    print(f"VISION field accuracy (reviewer B): {k}/{n} = {p:.3f} [{lo:.3f}, {hi:.3f}]")

    if args.reviewer_a:
        A = load(args.reviewer_a)
        sh = [u for u in A if u in B]
        if sh:
            a = [A[u] for u in sh]
            b = [B[u] for u in sh]
            m = len(sh)
            ag = sum(x == y for x, y in zip(a, b))
            po = ag / m
            pa, pb = sum(a) / m, sum(b) / m
            pe = pa * pb + (1 - pa) * (1 - pb)
            kappa = (po - pe) / (1 - pe) if pe != 1 else float("nan")
            pi = (pa + pb) / 2
            peg = 2 * pi * (1 - pi)
            ac1 = (po - peg) / (1 - peg) if peg != 1 else float("nan")
            print(f"inter-rater on {m}-row overlap: Po={po:.3f}, kappa={kappa:.3f}, AC1={ac1:.3f}, "
                  f"disagreements={m - ag}")

    # vision accuracy x text-agreement
    try:
        key = {r["row_uid"]: r for r in csv.DictReader(open(args.key))}
    except FileNotFoundError:
        return 0
    strata = {"corroborated by text rate": [], "plausibly graphical-only": []}
    for u, c in B.items():
        kr = key.get(u)
        if not kr:
            continue
        bucket = ("corroborated by text rate" if kr["close_text_rate_same_state_year"] == "True"
                  else "plausibly graphical-only")
        strata[bucket].append(c)
    print("\nvision accuracy by text-agreement:")
    for name, cs in strata.items():
        if cs:
            pp, llo, hhi = wilson(sum(cs), len(cs))
            print(f"  {name:28} {sum(cs)}/{len(cs)} = {pp:.3f} [{llo:.3f}, {hhi:.3f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
