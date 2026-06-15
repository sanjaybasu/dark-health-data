#!/usr/bin/env python3
"""Offline, zero-dependency demo of the Hidden Health Data pipeline.

Runs the full discover -> fetch -> extract -> validate -> curate -> publish flow
on the synthetic EQR fixtures using the deterministic rule extractor, then prints
where the outputs landed and shows the QA flags it caught.

    python scripts/run_demo.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.pipeline import run_dataset  # noqa: E402


def main() -> int:
    print("=" * 72)
    print("Hidden Health Data — offline demo (Medicaid EQR connector, rule extractor)")
    print("=" * 72)
    summary = run_dataset("eqr", extractor_name="rule", verbose=True)

    out_dir = Path(summary["out_dir"])
    measures = out_dir / "eqr_quality_measures.csv"
    print("\n--- sample of eqr_quality_measures.csv ---")
    with measures.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    cols = ["state", "plan_name", "measure_name", "rate", "numerator", "denominator", "qa_status"]
    print(" | ".join(cols))
    for r in rows[:6]:
        print(" | ".join(str(r.get(c, "")) for c in cols))

    flagged = [r for r in rows if r.get("qa_status") in {"warn", "fail"}]
    print(f"\n--- QA caught {len(flagged)} flagged row(s) ---")
    for r in flagged:
        print(f"  [{r['qa_status'].upper():4s}] {r['state']} {r['plan_name']} / {r['measure_name']}: {r['qa_flags']}")

    print("\nArtifacts written to:", out_dir)
    for p in sorted(out_dir.iterdir()):
        print("   ", p.name)
    print("\nNext: process a real report with the LLM extractor:")
    print("   cp registry/sources_eqr_live.yaml.example registry/sources_eqr_live.yaml")
    print("   pip install -e '.[all]' && export ANTHROPIC_API_KEY=...  # then:")
    print("   dhd run --dataset eqr --extractor llm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
