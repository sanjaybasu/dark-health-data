#!/usr/bin/env python3
"""Full-corpus EQR vision pass: read the FIGURE pages of every EQR document.

For each EQR document in the published dist, detect figure-dense pages, run the Claude
vision extractor over them, and reconcile the vision-read measures against the existing
text-extracted measures on the connector's ensemble key. Produces:

  * data/cache/vision/eqr.jsonl   -- the VISION-method records (for a merge/republish)
  * an audit summary to stdout    -- figure pages, vision records, net-new (graphical-only)
                                     measures, and text-vs-vision disagreements

Targeted + cheap: only figure pages are rendered (<=max_pages/doc). PDFs are read from the
content-addressed cache (settings.raw_dir); the few not cached are fetched on demand.

Usage:
  ANTHROPIC_API_KEY=... python scripts/eqr_vision_pass.py [--model claude-haiku-4-5]
                                                          [--max-pages 12] [--workers 5]
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402
from dark_health_data.connectors.eqr import EQRConnector  # noqa: E402
from dark_health_data.extract.vision import ClaudeVisionExtractor  # noqa: E402
from dark_health_data.models import EQRQualityMeasure, SourceDocument  # noqa: E402


def _load_dist():
    z = sorted(glob.glob("dist/eqr-v*.zip"))[-1]
    zf = zipfile.ZipFile(z)
    dc = zf.read([n for n in zf.namelist() if n.endswith("documents.csv")][0]).decode()
    docs = list(csv.DictReader(io.StringIO(dc)))
    rj = [n for n in zf.namelist() if n.endswith("records.jsonl")][0]
    recs = [json.loads(ln) for ln in zf.read(rj).decode().splitlines() if ln.strip()]
    return z, docs, recs


def _locate_pdf(document_id: str, source_url: str | None) -> str | None:
    # prefer a real PDF; never return the .meta.json provenance sidecar
    hits = [h for h in glob.glob(os.path.join(str(settings.raw_dir), f"{document_id}*"))
            if not h.endswith(".meta.json")]
    hits.sort(key=lambda h: (not h.lower().endswith(".pdf"), len(h)))
    if hits:
        return hits[0]
    if not source_url:
        return None
    try:
        from dark_health_data.fetch import _download
        dest = settings.raw_dir / f"{document_id}.pdf"
        dest.write_bytes(_download(source_url))
        return str(dest)
    except Exception as exc:
        print(f"  ! fetch failed for {document_id[:10]} ({exc})", file=sys.stderr)
        return None


def _key(measure_dict_or_rec):
    """Text-record ensemble key from a dist record dict (mirrors connector.ensemble_key)."""
    r = measure_dict_or_rec
    return ("eqr_m", r.get("state"), r.get("plan_name"), r.get("measure_name"),
            r.get("population"), r.get("reporting_year"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default="data/cache/vision/eqr.jsonl")
    args = ap.parse_args()

    _, docs, recs = _load_dist()
    text_measures = [r for r in recs if r.get("record_type") == "eqr_quality_measure"]
    text_keys = {_key(r): r for r in text_measures}
    print(f"EQR dist: {len(docs)} docs, {len(text_measures):,} text quality-measures")

    # resume: skip docs already written to the output (per-doc partial save below)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done_path = out.with_suffix(".done")  # document_ids fully processed (even if 0 measures)
    done: set[str] = set()
    if done_path.exists():
        done = {x for x in done_path.read_text().splitlines() if x.strip()}
        print(f"resume: {len(done)} doc(s) already done -> skipping")
    docs = [d for d in docs if d["document_id"] not in done]

    connector = EQRConnector()

    def process(d):
        did = d["document_id"]
        pdf = _locate_pdf(did, d.get("source_url"))
        if not pdf:
            return did, None, []
        try:
            pages = ClaudeVisionExtractor.figure_pages(pdf, max_pages=args.max_pages)
        except Exception as exc:
            print(f"  ! {d.get('jurisdiction')} figure-detect failed ({exc})", file=sys.stderr)
            return did, 0, []
        if not pages:
            return did, 0, []
        doc = SourceDocument(document_id=did, dataset_id="eqr", source_url=d.get("source_url"),
                             local_path=pdf, jurisdiction=d.get("jurisdiction"),
                             program=d.get("program"),
                             report_year=int(d["report_year"]) if d.get("report_year") else None)
        ex = ClaudeVisionExtractor(model=args.model, max_pages=args.max_pages,
                                   escalate_model=None, pages=pages)
        recs_v = [r for r in ex.extract("", doc, connector) if isinstance(r, EQRQualityMeasure)]
        print(f"  {d.get('jurisdiction'):<4} {len(pages):>2} figure page(s) -> {len(recs_v)} vision measure(s)")
        return did, len(pages), recs_v

    import threading
    lock = threading.Lock()
    n_pages = 0
    fh = open(out, "a")  # append: partial-save + resume-safe
    dh = open(done_path, "a")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for did, npg, vrecs in pool.map(process, docs):
            with lock:
                if npg:
                    n_pages += npg
                for v in vrecs:
                    fh.write(json.dumps(v.model_dump(mode="json")) + "\n")
                if npg is not None:  # doc processed (had a usable PDF); mark done
                    dh.write(did + "\n")
                fh.flush()
                dh.flush()
    fh.close()
    dh.close()

    # reconcile the FULL output (resumed + new) against the text records
    matched = disagree = netnew = total = 0
    for line in out.read_text().splitlines():
        if not line.strip():
            continue
        total += 1
        r = json.loads(line)
        k = ("eqr_m", r.get("state"), r.get("plan_name"), r.get("measure_name"),
             r.get("population"), r.get("reporting_year"))
        t = text_keys.get(k)
        if t is None:
            netnew += 1
        else:
            matched += 1
            tv, vv = t.get("rate"), r.get("rate")
            if tv is not None and vv is not None and abs(tv - vv) > max(0.1, 0.01 * abs(tv)):
                disagree += 1

    print("\n=== EQR vision pass audit ===")
    print(f"figure pages rendered this run : {n_pages}")
    print(f"vision measures (total)        : {total:,}")
    print(f"  matched to a text measure    : {matched:,}")
    print(f"    agree on rate              : {matched - disagree:,}")
    print(f"    DISAGREE on rate (flag)    : {disagree:,}")
    print(f"  net-new (graphical-only)     : {netnew:,}")
    print(f"\nwrote vision records -> {out}")
    print(f"est. cost this run ~ ${n_pages * 0.005:.2f} (Haiku, ~$0.005/figure page)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
