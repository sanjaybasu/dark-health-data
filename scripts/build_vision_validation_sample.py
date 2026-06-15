#!/usr/bin/env python3
"""Draw a blinded, stratified random sample of VISION-extracted records for human validation.

This is the vision-stage counterpart to the text validation sheet: a fresh random sample
(stratified by state) of figure-derived records, with the source figure's URL and page so a
reviewer can score each value against the chart. It is deliberately NOT the re-adjudication
sheet or the disagreement list (both are biased subsets) — an unbiased random sample is what
estimates vision accuracy.

Output: an xlsx with an Instructions sheet and a Review sheet whose `correct` column is blank
for the reviewer. A hidden `row_uid` lets us merge scores back; nothing reveals whether the
text extractor agreed (blinding).

Usage:
  python scripts/build_vision_validation_sample.py [--n 150] [--seed 20260615]
      [--in data/cache/vision/eqr.jsonl] [--out private/review-packet/eqr_vision_validation_sample.xlsx]
"""
from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path

import openpyxl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20260615)
    ap.add_argument("--in", dest="inp", default="data/cache/vision/eqr.jsonl")
    ap.add_argument("--out", default="private/review-packet/eqr_vision_validation_sample.xlsx")
    args = ap.parse_args()

    recs = [json.loads(ln) for ln in open(args.inp) if ln.strip()]
    pool = [r for r in recs if r.get("rate") is not None]  # only rate-bearing reads are scorable

    # stratify by state, allocate proportionally (>=1 per state with any records)
    by_state: dict[str, list] = collections.defaultdict(list)
    for r in pool:
        by_state[r.get("state") or "??"].append(r)
    rng = random.Random(args.seed)
    total = len(pool)
    picked = []
    for st, rows in by_state.items():
        k = max(1, round(args.n * len(rows) / total))
        picked.extend(rng.sample(rows, min(k, len(rows))))
    rng.shuffle(picked)
    picked = picked[: args.n]

    wb = openpyxl.Workbook()
    ins = wb.active
    ins.title = "Instructions"
    for line in [
        "Vision-extraction validation — reviewer instructions (one page)",
        "",
        "You are checking whether values an AI read from a FIGURE (chart/graph) on a report page",
        "match the figure itself. About %d rows." % len(picked),
        "",
        "What each row is",
        "One value the vision model read from a figure: a measure, the population, the year, and the",
        "rate. prov_source_url is the source PDF; prov_page_start is the page with the figure.",
        "",
        "Your task: fill the `correct` column",
        "1. Open prov_source_url and go to page prov_page_start; find the figure.",
        "2. Read the value for that measure / series / year off the figure.",
        "3. Enter one of:",
        "   1 — the extracted value matches the figure (same number within rounding, ~±0.2) and is",
        "       attributed to the right measure/series, population, and year.",
        "   0 — anything is off: wrong number, wrong series/population/year, or not on the figure.",
        "   blank — only if you genuinely cannot find the value or open the source.",
        "Fill only the `correct` column; leave the hidden row_uid and other columns unchanged.",
        "Optional: note any systematic patterns (e.g., 'misreads the most crowded data labels').",
    ]:
        ins.append([line])

    rev = wb.create_sheet("Review")
    cols = ["row_uid", "state", "plan_name", "measure_name", "population", "reporting_year",
            "rate", "prov_source_url", "prov_page_start", "correct"]
    rev.append(cols)
    for i, r in enumerate(picked, 1):
        pv = r.get("provenance", {})
        rev.append([
            f"vis-{i:04d}", r.get("state"), r.get("plan_name"), r.get("measure_name"),
            r.get("population"), r.get("reporting_year"), r.get("rate"),
            pv.get("source_url"), pv.get("page_start"), "",
        ])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    # Unblinded KEY (NOT given to the reviewer): row_uid -> text-agreement, for the
    # comprehensive analysis after scores come back (vision accuracy x text-agreement).
    import csv
    import glob
    import zipfile

    text_by_key, text_by_sy = {}, collections.defaultdict(list)
    zips = sorted(glob.glob("dist/eqr-v*.zip"))
    if zips:
        zf = zipfile.ZipFile(zips[-1])
        rj = [n for n in zf.namelist() if n.endswith("records.jsonl")][0]
        for tr in (json.loads(x) for x in zf.read(rj).decode().splitlines() if x.strip()):
            if tr.get("record_type") != "eqr_quality_measure":
                continue
            k = (tr.get("state"), tr.get("plan_name"), tr.get("measure_name"),
                 tr.get("population"), tr.get("reporting_year"))
            text_by_key[k] = tr.get("rate")
            if tr.get("rate") is not None:
                text_by_sy[(tr.get("state"), tr.get("reporting_year"))].append(tr["rate"])

    keypath = out.with_name("eqr_vision_validation_KEY.csv")
    with open(keypath, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["row_uid", "state", "measure_name", "reporting_year", "vision_rate",
                    "exact_text_match", "text_rate", "close_text_rate_same_state_year", "page", "source_url"])
        for i, r in enumerate(picked, 1):
            pv = r.get("provenance", {})
            k = (r.get("state"), r.get("plan_name"), r.get("measure_name"),
                 r.get("population"), r.get("reporting_year"))
            tr = text_by_key.get(k)
            vr = r.get("rate")
            close = any(abs(vr - t) <= max(0.15, 0.01 * abs(t))
                        for t in text_by_sy.get((r.get("state"), r.get("reporting_year")), []))
            w.writerow([f"vis-{i:04d}", r.get("state"), r.get("measure_name"), r.get("reporting_year"),
                        vr, tr is not None, tr, close, pv.get("page_start"), pv.get("source_url")])

    print(f"wrote {len(picked)} rows across {len(by_state)} states -> {out}")
    print(f"unblinded key -> {keypath}")
    print(f"seed={args.seed} (reproducible); vision model: "
          f"{picked[0].get('provenance', {}).get('model_name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
