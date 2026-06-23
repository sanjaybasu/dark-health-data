#!/usr/bin/env python3
"""Local, zero-Claude figure extraction: vector-text labels + local VLM attribution.

For each figure page in Seth's validation sample:
  1. pull the exact numeric data labels from the PDF vector text layer (pymupdf) -- free, exact;
     if the page has none (rasterized labels), OCR the rendered page with tesseract -- free;
  2. send the rendered figure + that exact-value list to a LOCAL vision model via Ollama
     (OpenAI-compatible / native API), asking it only to ASSIGN each value to its
     series/category/year. The model never has to read digits, only attribute them.

Runs entirely on the local machine (Ollama + tesseract); no Claude/Anthropic calls. Produces a
comparison CSV and a re-adjudication sheet, plus an objective signal: of the cases the original
extraction got wrong, how many were value-misreads that the exact vector text now eliminates.

Usage:  python scripts/vision_local_hybrid.py [--model qwen2.5vl:7b]
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

KEY = "private/review-packet/eqr_vision_validation_KEY.csv"
SETH = "/Users/sanjaybasu/waymark-local/notebooks/dark-health-data/eqr_vision_validation_reviewerB_berkowitz (completed).xlsx"
CACHE = Path("/tmp/vision_lift")
NUMPAT = re.compile(r"^\d{1,3}(\.\d+)?%?$")
OLLAMA = "http://localhost:11434/api/chat"


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def vector_labels(doc, page):
    words = doc[page - 1].get_text("words")
    return [w[4].rstrip("%") for w in words if NUMPAT.match(w[4])]


def ocr_labels(doc, page):
    try:
        import pytesseract
        from PIL import Image
        import io
        pix = doc[page - 1].get_pixmap(dpi=200)
        txt = pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png"))))
        return [t.rstrip("%") for t in txt.split() if NUMPAT.match(t)]
    except Exception:
        return []


def attribute(img_b64, labels, model):
    prompt = (
        "This image is a figure (chart) from a health report. The exact numeric values printed "
        f"on it are: {labels}. For each plotted value, identify its series/legend label, its "
        "category or cohort, and its year if shown on an axis. Use ONLY the exact values listed; "
        "do not invent numbers. Respond as JSON: "
        '{"records":[{"series":"","category":"","year":null,"value":null}]}'
    )
    try:
        r = requests.post(OLLAMA, timeout=180, json={
            "model": model, "format": "json", "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [img_b64]}]})
        return json.loads(r.json()["message"]["content"]).get("records", []) or []
    except Exception as e:
        print(f"  ! ollama error: {e}", file=sys.stderr)
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5vl:7b")
    ap.add_argument("--out", default="private/review-packet/eqr_local_hybrid.csv")
    args = ap.parse_args()
    import fitz
    import hashlib
    from dark_health_data.fetch import _download

    key = {r["row_uid"]: r for r in csv.DictReader(open(KEY))}
    ws = openpyxl.load_workbook(SETH, data_only=True)["Review"]
    rows = list(ws.iter_rows(values_only=True))
    H = {str(h): i for i, h in enumerate(rows[0])}
    seth = {}
    for r in rows[1:]:
        try:
            seth[str(r[H["row_uid"]])] = int(str(r[H["correct"]]).strip())
        except (TypeError, ValueError):
            pass

    by_page = defaultdict(list)
    for uid, k in key.items():
        by_page[(k["source_url"], int(k["page"]))].append(uid)

    def pdf_for(url):
        CACHE.mkdir(exist_ok=True)
        d = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:12] + ".pdf")
        if not d.exists() or d.stat().st_size < 1000:
            d.write_bytes(_download(url))
        return d

    out_rows = []
    for (url, page), uids in by_page.items():
        try:
            doc = fitz.open(pdf_for(url))
        except Exception:
            continue
        labels = vector_labels(doc, page)
        source = "vector"
        if not labels:
            labels = ocr_labels(doc, page)
            source = "ocr"
        img = base64.standard_b64encode(doc[page - 1].get_pixmap(dpi=200).tobytes("png")).decode()
        recs = attribute(img, labels[:60], args.model) if labels else []
        labelset = set(labels)
        for uid in uids:
            k = key[uid]
            tm, yr = _norm(k["measure_name"]), k["reporting_year"]
            # match the VLM's attributed records to this gold (measure, year)
            hit = None
            for v in recs:
                vm = _norm(v.get("series")) + _norm(v.get("category"))
                if (tm[:8] in vm or vm[:8] in tm) and str(v.get("year")) == str(yr):
                    hit = v
                    break
            new_val = hit.get("value") if hit else None
            out_rows.append({
                "row_uid": uid, "state": k["state"], "measure": k["measure_name"][:40],
                "year": yr, "old_value": k["vision_rate"], "seth_score": seth.get(uid),
                "label_source": source, "n_labels": len(labels),
                "new_value": new_val, "new_value_is_exact_label": str(new_val).rstrip("%") in labelset if new_val is not None else False,
                "page": page, "source_url": url})
        doc.close()
        print(f"  {key[uids[0]]['state']} p{page}: {len(labels)} {source} labels, VLM attributed {len(recs)}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    errs = [r for r in out_rows if r["seth_score"] == 0]
    got = [r for r in errs if r["new_value"] is not None]
    exact = [r for r in errs if r["new_value_is_exact_label"]]
    vec = sum(1 for r in out_rows if r["label_source"] == "vector")
    print(f"\n=== local hybrid ({args.model}), $0 Claude ===")
    print(f"pages with vector labels: {vec}/{len(out_rows)} rows")
    print(f"Seth errors: {len(errs)} | local hybrid produced a new attributed value: {len(got)}"
          f" | of those, value is an exact printed label: {len(exact)}")
    print(f"wrote {args.out} (build re-adjudication sheet from this for the real lift number)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
