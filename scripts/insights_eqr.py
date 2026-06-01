#!/usr/bin/env python3
"""Example cross-state insights from the curated EQR dataset (run after `dhd run`).

Prints coverage, trust/QA summary, the most common measures, a cross-state comparison
for a few common measures, and the compliance-determination mix. Stdlib only.

    python scripts/insights_eqr.py
"""

from __future__ import annotations

import csv
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed" / "eqr"


def _load(name: str) -> list[dict]:
    p = PROC / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> int:
    measures = _load("eqr_quality_measures.csv")
    if not measures:
        print(f"No data at {PROC}. Run `dhd run --dataset eqr --extractor llm` first.")
        return 1

    states = sorted({r["state"] for r in measures if r.get("state")})
    plans = {(r["state"], r["plan_name"]) for r in measures}
    print("=" * 70)
    print("EQR DATASET — COVERAGE")
    print("=" * 70)
    print(f"states: {len(states)} ({', '.join(states)})")
    print(f"plan-rows: {len(plans)} distinct (state, plan)")
    print(f"measure rows: {len(measures)}")
    print(f"distinct measures: {len({r['measure_name'] for r in measures})}")

    trusts = [t for r in measures if (t := _num(r.get('trust_score'))) is not None]
    qa = Counter(r.get("qa_status") for r in measures)
    review = sum(1 for r in measures if str(r.get("review_recommended")).lower() == "true")
    print("\n--- trust / QA ---")
    print(f"mean trust: {st.mean(trusts):.3f}" if trusts else "mean trust: n/a")
    print(f"qa_status: {dict(qa)}")
    print(f"review_recommended: {review} ({100*review/len(measures):.0f}%)")

    print("\n--- most common measures (top 12) ---")
    for name, c in Counter(r["measure_name"] for r in measures).most_common(12):
        print(f"  {c:4d}  {name[:60]}")

    # cross-state comparison for a few common measures (percent, non-failed rows)
    keywords = ["Controlling High Blood Pressure", "Well-Child", "Postpartum",
                "Breast Cancer", "Follow-Up After Hospitalization", "Child and Adolescent Well"]
    print("\n--- cross-state mean rate for common measures (percent, qa!=fail) ---")
    for kw in keywords:
        by_state: dict[str, list[float]] = defaultdict(list)
        for r in measures:
            if (kw.lower() in (r.get("measure_name") or "").lower()
                    and (r.get("rate_unit") == "percent")
                    and r.get("qa_status") != "fail"):
                v = _num(r.get("rate"))
                if v is not None:
                    by_state[r["state"]].append(v)
        if by_state:
            cells = sorted(((s, st.mean(v)) for s, v in by_state.items()), key=lambda x: -x[1])
            line = "  ".join(f"{s}:{m:.0f}" for s, m in cells)
            print(f"  {kw[:34]:34s} | {line}")

    comp = _load("eqr_compliance_findings.csv")
    if comp:
        print("\n--- compliance determinations ---")
        for det, c in Counter(r.get("determination") for r in comp).most_common():
            print(f"  {c:4d}  {det}")

    pips = _load("eqr_performance_improvement_projects.csv")
    print(f"\nPIPs: {len(pips)} | compliance findings: {len(comp)}")
    print("\n(Descriptive only — validate against source PDFs before publication.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
