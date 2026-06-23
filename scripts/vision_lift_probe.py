#!/usr/bin/env python3
"""Targeted prototype: does a stronger reader recover the figure-extraction errors?

Re-extracts the specific (pdf, page, measure, year) cases a human reviewer scored wrong,
under two conditions, to estimate the lift before committing to a corpus re-run:

  C0 (baseline)  : Haiku, 200 dpi, current vision prompt  -- should reproduce the error
  C1 (candidate) : Sonnet, 300 dpi, tightened attribution prompt (name the series/legend +
                   axis-year for every value; read the FIGURE only, never a table on the page)

It also re-extracts a random sample of rows the reviewer scored CORRECT, as a regression
control (does C1 break ones that were right?).

Auto-scores cases that have an exact-key text match (the text rate is a reliable ground-truth
proxy): C1 is "fixed" if its value now matches that text rate. Cases without a text anchor are
written to a re-adjudication sheet for a short human pass. Isolated from the production
extractor on purpose -- it calls the vision API directly with per-condition prompts.

Usage:
  ANTHROPIC_API_KEY=... python scripts/vision_lift_probe.py [--controls 30] [--seed 20260623]
"""
from __future__ import annotations

import argparse
import base64
import csv
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dark_health_data.connectors.eqr import EQRConnector  # noqa: E402

SETH = "/Users/sanjaybasu/waymark-local/notebooks/dark-health-data/eqr_vision_validation_reviewerB_berkowitz (completed).xlsx"
KEY = "private/review-packet/eqr_vision_validation_KEY.csv"
CACHE = Path("/tmp/vision_lift")
CACHE.mkdir(exist_ok=True)

BASE_PROMPT = (
    "You are reading a RENDERED IMAGE of a report page that contains a figure (chart/graph). "
    "Read the plotted values and attribute each to the right series, category, and year."
)
TIGHT_PROMPT = (
    "You are reading a RENDERED IMAGE of a report page. Extract values ONLY from the FIGURE "
    "(chart/graph) on the page; do NOT read any value from a table or body text on the same "
    "page. For every value you report, you must be able to identify, from the figure itself, "
    "(a) which series/legend entry it belongs to, (b) which category/cohort/bar it sits on, and "
    "(c) which year on the axis it corresponds to. Emit one record per (series, category, year). "
    "Read data labels exactly as printed; if a label is too small or overlapping to read with "
    "confidence, omit it rather than guess. Do not infer an unlabelled point."
)


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _match_rate(measures, target_measure, target_year):
    """Pick the re-extracted measure matching the target (fuzzy name + exact year); return rate."""
    tm = _norm(target_measure)
    cands = []
    for m in measures:
        nm = _norm(m.get("measure_name"))
        if not nm:
            continue
        overlap = nm in tm or tm in nm or any(nm[i:i + 6] in tm for i in range(len(nm) - 5))
        if overlap and (target_year is None or m.get("reporting_year") == target_year):
            cands.append(m)
    if not cands:
        return None
    rated = [m for m in cands if m.get("rate") is not None]
    return (rated or cands)[0].get("rate")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--controls", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20260623)
    ap.add_argument("--out", default="private/review-packet/eqr_vision_lift_probe.csv")
    args = ap.parse_args()

    import anthropic
    import fitz
    from dark_health_data.config import settings
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    schema = EQRConnector().extraction_schema()
    tool = {"name": "emit_records", "description": "Return all records read from this figure page.",
            "input_schema": schema}

    # labels
    ws = openpyxl.load_workbook(SETH, data_only=True)["Review"]
    rows = list(ws.iter_rows(values_only=True))
    H = {str(h): i for i, h in enumerate(rows[0])}
    label = {}
    for r in rows[1:]:
        try:
            label[str(r[H["row_uid"]])] = int(str(r[H["correct"]]).strip())
        except (TypeError, ValueError):
            pass
    key = {r["row_uid"]: r for r in csv.DictReader(open(KEY))}
    errors = [u for u, v in label.items() if v == 0]
    correct = [u for u, v in label.items() if v == 1]
    controls = random.Random(args.seed).sample(correct, min(args.controls, len(correct)))
    targets = [(u, "error") for u in errors] + [(u, "control") for u in controls]
    print(f"probe: {len(errors)} errors + {len(controls)} controls = {len(targets)} cases x 2 conditions")

    def pdf_for(url):
        import hashlib
        d = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".pdf")
        if not d.exists() or d.stat().st_size < 1000:
            from dark_health_data.fetch import _download
            d.write_bytes(_download(url))
        return d

    def render(url, page, dpi):
        with fitz.open(pdf_for(url)) as doc:
            return base64.standard_b64encode(doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")).decode()

    def call(model, img, prompt):
        for attempt in range(8):
            try:
                resp = client.messages.create(
                    model=model, max_tokens=4096, system=prompt, tools=[tool],
                    tool_choice={"type": "tool", "name": "emit_records"},
                    messages=[{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}},
                        {"type": "text", "text": "Read every value from the figure(s) on this page."}]}])
                for b in resp.content:
                    if getattr(b, "type", None) == "tool_use" and b.name == "emit_records":
                        return b.input.get("quality_measures", []) or []
                return []
            except (anthropic.RateLimitError, anthropic.APIConnectionError):
                time.sleep(min(2 ** attempt, 30))
            except anthropic.APIStatusError as e:
                if 500 <= getattr(e, "status_code", 0) < 600:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                return []
        return []

    def process(item):
        uid, kind = item
        k = key.get(uid, {})
        url, page = k.get("source_url"), int(k.get("page"))
        yr = int(k["reporting_year"]) if k.get("reporting_year") else None
        meas = k.get("measure_name")
        c0 = _match_rate(call("claude-haiku-4-5", render(url, page, 200), BASE_PROMPT), meas, yr)
        c1 = _match_rate(call("claude-sonnet-4-6", render(url, page, 300), TIGHT_PROMPT), meas, yr)
        return {"row_uid": uid, "kind": kind, "state": k.get("state"), "measure_name": meas,
                "reporting_year": yr, "old_vision_rate": k.get("vision_rate"),
                "c0_haiku_rate": c0, "c1_sonnet_rate": c1,
                "exact_text_match": k.get("exact_text_match"), "text_rate": k.get("text_rate"),
                "page": page, "source_url": url}

    out_rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i, r in enumerate(pool.map(process, targets), 1):
            out_rows.append(r)
            print(f"  [{i}/{len(targets)}] {r['kind']:<7} {r['row_uid']} old={r['old_vision_rate']} "
                  f"c0={r['c0_haiku_rate']} c1={r['c1_sonnet_rate']}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    # auto-score where an exact-key text rate exists (reliable ground-truth proxy)
    def close(a, b):
        try:
            a, b = float(a), float(b)
            return abs(a - b) <= max(0.2, 0.01 * abs(b))
        except (TypeError, ValueError):
            return False
    auto = [r for r in out_rows if r["kind"] == "error" and str(r["exact_text_match"]) == "True" and r["text_rate"]]
    fixed = [r for r in auto if close(r["c1_sonnet_rate"], r["text_rate"])]
    c0_fixed = [r for r in auto if close(r["c0_haiku_rate"], r["text_rate"])]
    print(f"\n=== auto-scored errors (exact text anchor): {len(auto)} ===")
    print(f"  C1 (Sonnet) now matches text: {len(fixed)}/{len(auto)}")
    print(f"  C0 (Haiku)  matches text:     {len(c0_fixed)}/{len(auto)} (baseline reproduces the error)")
    need_human = [r for r in out_rows if not (r["kind"] == "error" and str(r["exact_text_match"]) == "True" and r["text_rate"])]
    print(f"  needs human re-adjudication: {len(need_human)} rows (graphical-only errors + controls)")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
