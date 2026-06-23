#!/usr/bin/env python3
"""Preliminary recall estimate for the TEXT pipeline (did it find the records that are there?).

Recall needs a ground-truth count of all true records on a sample. Lacking a human enumeration,
this gives an automated estimate: on a stratified sample of measure-bearing pages, an INDEPENDENT
exhaustive re-extraction (Sonnet, recall-maximising prompt, different model than the Haiku
pipeline) serves as the reference, and we measure the fraction of reference records the published
pipeline already contains. It is an estimate, not gold: the reference can miss rows (inflating
recall) or over-list (deflating it), so the misses are written out for a human spot-check.

Outputs a per-page CSV and a summary (mean page recall, and the records the reference found that
the pipeline lacks). Reads cached EQR PDFs from settings.raw_dir.

Usage:
  ANTHROPIC_API_KEY=... python scripts/recall_estimate.py [--pages 25] [--seed 20260623]
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os
import random
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dark_health_data.config import settings  # noqa: E402

REF_PROMPT = (
    "You are reading the text of ONE page from a Medicaid External Quality Review report. List "
    "EVERY performance-measure value on this page: one record per (plan, measure, population, "
    "year) with a rate. Be exhaustive. Include every row of every table, including long appendix "
    "tables. Do not skip rows. Copy values verbatim; do not compute or infer. If the page has no "
    "performance-measure values, return an empty list."
)
SCHEMA = {"type": "object", "properties": {"quality_measures": {"type": "array", "items": {
    "type": "object", "properties": {
        "plan_name": {"type": ["string", "null"]}, "measure_name": {"type": ["string", "null"]},
        "population": {"type": ["string", "null"]}, "reporting_year": {"type": ["integer", "null"]},
        "rate": {"type": ["number", "null"]}}}}}, "required": ["quality_measures"]}


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _key(m):
    return (_norm(m.get("plan_name"))[:24], _norm(m.get("measure_name"))[:40], m.get("reporting_year"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=25)
    ap.add_argument("--seed", type=int, default=20260623)
    ap.add_argument("--out", default="private/review-packet/eqr_recall_estimate.csv")
    args = ap.parse_args()

    import anthropic
    import fitz
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tool = {"name": "emit", "description": "Exhaustive list of measure values on the page.",
            "input_schema": SCHEMA}

    # pipeline records by (doc, page)
    z = sorted(glob.glob("dist/eqr-v*.zip"))[-1]
    zf = zipfile.ZipFile(z)
    recs = [json.loads(ln) for ln in zf.read([n for n in zf.namelist()
            if n.endswith("records.jsonl")][0]).decode().splitlines() if ln.strip()]
    docs = list(csv.DictReader(io.StringIO(zf.read([n for n in zf.namelist()
            if n.endswith("documents.csv")][0]).decode())))
    docmeta = {d["document_id"]: d for d in docs}
    by_pp = defaultdict(set)
    for r in recs:
        if r.get("record_type") == "eqr_quality_measure":
            pv = r.get("provenance", {})
            if pv.get("page_start"):
                by_pp[(pv["source_document_id"], int(pv["page_start"]))].add(_key(r))

    def locate(did):
        h = [x for x in glob.glob(os.path.join(str(settings.raw_dir), f"{did}*")) if not x.endswith(".meta.json")]
        return h[0] if h else None

    # sample measure-bearing pages, stratified across docs
    pages = [pp for pp in by_pp if len(by_pp[pp]) >= 1 and locate(pp[0])]
    rng = random.Random(args.seed)
    rng.shuffle(pages)
    by_doc, sample = defaultdict(int), []
    for pp in pages:
        if by_doc[pp[0]] < 3:  # at most 3 pages/doc for spread
            sample.append(pp)
            by_doc[pp[0]] += 1
        if len(sample) >= args.pages:
            break
    print(f"recall sample: {len(sample)} measure-bearing pages across {len(by_doc)} reports")

    def page_text(did, page):
        p = locate(did)
        with fitz.open(p) as doc:
            return doc[page - 1].get_text() or ""

    def ref_extract(text):
        for attempt in range(6):
            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-6", max_tokens=8000, system=REF_PROMPT, tools=[tool],
                    tool_choice={"type": "tool", "name": "emit"},
                    messages=[{"role": "user", "content": text[:60000]}])
                for b in resp.content:
                    if getattr(b, "type", None) == "tool_use":
                        return b.input.get("quality_measures", []) or []
                return []
            except (anthropic.RateLimitError, anthropic.APIConnectionError):
                time.sleep(min(2 ** attempt, 30))
            except anthropic.APIStatusError:
                return []
        return []

    out_rows, misses = [], []
    for did, page in sample:
        ref = ref_extract(page_text(did, page))
        ref_keys = {_key(m) for m in ref if m.get("rate") is not None}
        pipe_keys = by_pp[(did, page)]

        def match(rk, pipe=pipe_keys):  # fuzzy: same year + overlapping plan & measure tokens
            for pk in pipe:
                if rk[2] == pk[2] and (rk[1] in pk[1] or pk[1] in rk[1] or rk[1][:10] == pk[1][:10]):
                    return True
            return False
        found = [rk for rk in ref_keys if match(rk)]
        missed = [rk for rk in ref_keys if not match(rk)]
        rec = len(found) / len(ref_keys) if ref_keys else None
        st = docmeta.get(did, {}).get("jurisdiction")
        out_rows.append({"state": st, "page": page, "reference_records": len(ref_keys),
                         "pipeline_has": len(found), "missed": len(missed),
                         "page_recall": f"{rec:.3f}" if rec is not None else ""})
        for mk in missed[:5]:
            misses.append({"state": st, "page": page, "plan": mk[0], "measure": mk[1], "year": mk[2]})
        print(f"  {st} p{page}: ref={len(ref_keys)} pipeline_has={len(found)} missed={len(missed)}"
              + (f" recall={rec:.2f}" if rec is not None else ""))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    mp = args.out.replace(".csv", "_misses.csv")
    if misses:
        with open(mp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(misses[0].keys()))
            w.writeheader()
            w.writerows(misses)

    # pooled recall (sum found / sum reference) + simple mean of page recalls
    tot_ref = sum(r["reference_records"] for r in out_rows)
    tot_found = sum(r["pipeline_has"] for r in out_rows)
    pooled = tot_found / tot_ref if tot_ref else 0
    pr = [float(r["page_recall"]) for r in out_rows if r["page_recall"]]
    print("\n=== preliminary recall estimate (reference = independent Sonnet exhaustive read) ===")
    print(f"pages={len(out_rows)}  reference records={tot_ref}  pipeline matched={tot_found}")
    print(f"POOLED recall = {pooled:.3f} | mean page recall = {sum(pr)/len(pr):.3f}" if pr else "")
    print(f"misses written to {mp} for human spot-check (reference may over-list; verify before quoting)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
