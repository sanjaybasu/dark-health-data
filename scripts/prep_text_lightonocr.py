#!/usr/bin/env python3
"""Build the per-dataset text cache with local LightOnOCR for bad pages (no API cost).

For each candidate document: fetch it, read the PDF text layer page by page, and OCR a
page with LightOnOCR-3-0.8B (MLX, on the Mac GPU) when the page has no text layer or
when its text layer is garbled (an old, poor OCR layer). Writes the same cache that
``dhd batch`` already reads: data/cache/modal_text/<dataset>.jsonl (URL -> text).
Resumable: URLs already in the cache are skipped.

Needs mlx-vlm and pymupdf (Apple silicon). Usage:
  python scripts/prep_text_lightonocr.py <dataset> [--limit N] [--sample N --seed S]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import tempfile
import time
from pathlib import Path

from dark_health_data.config import settings
from dark_health_data.fetch import fetch
from dark_health_data.pdf import _read_pages, _with_marker
from dark_health_data.discovery import discover

MLX_MODEL = "prithivMLmods/LightOnOCR-3-0.8B-MLX"
MIN_CHARS = 20
# A page is garbled when more than this share of its letter tokens are 1-2 letters
# long. Calibrated on CT decisions: clean pages <= 0.273, garbled OCR layers >= 0.322.
MAX_SHORT_SHARE = 0.30


def short_share(text: str) -> float:
    toks = re.findall(r"[A-Za-z]+", text)
    return sum(len(t) <= 2 for t in toks) / len(toks) if toks else 0.0


def needs_ocr(text: str) -> bool:
    return len(text.strip()) < MIN_CHARS or short_share(text) > MAX_SHORT_SHARE


class LightOnOCR:
    def __init__(self, model_id: str = MLX_MODEL):
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template

        self._generate = generate
        self.model, self.processor = load(model_id)
        self.prompt = apply_chat_template(self.processor, self.model.config, "",
                                          num_images=1, enable_thinking=False)

    def page(self, pdf_path: str, page_no: int) -> str:
        import pymupdf

        pg = pymupdf.open(pdf_path)[page_no - 1]
        # 400 DPI, longest edge capped at 2,048 px (the pilot setting)
        zoom = min(400 / 72, 2048 / max(pg.rect.width, pg.rect.height))
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(tmp.name)
            res = self._generate(self.model, self.processor, self.prompt, image=[tmp.name],
                                 max_tokens=4096, temperature=0.2, top_p=0.9, verbose=False)
        return getattr(res, "text", res)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", type=int, default=None, help="random sample of N candidates")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    cands = [c for c in discover(a.dataset) if c.url]
    if a.sample:
        cands = random.Random(a.seed).sample(cands, min(a.sample, len(cands)))
    if a.limit:
        cands = cands[: a.limit]
    out = settings.cache_dir / "modal_text" / f"{a.dataset}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(line)["url"] for line in out.open() if line.strip()}
    ocr = None
    n_pages = n_ocr = 0
    t0 = time.time()
    with out.open("a") as f:
        for i, cand in enumerate(cands, 1):
            if cand.url in done:
                continue
            time.sleep(1.0)  # be polite to the source server
            try:
                doc = fetch(cand)
                pages = _read_pages(Path(doc.local_path))
            except Exception as exc:  # noqa: BLE001 - record and move on
                f.write(json.dumps({"url": cand.url, "ok": False, "error": str(exc)[:200]}) + "\n")
                continue
            ocr_pages = []
            for p, body in enumerate(pages, 1):
                if needs_ocr(body):
                    ocr = ocr or LightOnOCR()
                    pages[p - 1] = ocr.page(doc.local_path, p) or body
                    ocr_pages.append(p)
            text = "".join(_with_marker(p, b) for p, b in enumerate(pages, 1))
            f.write(json.dumps({"url": cand.url, "ok": True, "document_id": doc.document_id,
                                "text": text, "n_pages": len(pages),
                                "ocr_pages": ocr_pages, "ocr_model": MLX_MODEL if ocr_pages else None}) + "\n")
            f.flush()
            n_pages += len(pages)
            n_ocr += len(ocr_pages)
            print(f"{i}/{len(cands)} {cand.title}: {len(pages)} pp, OCR {ocr_pages} "
                  f"[{time.time() - t0:.0f}s total]", flush=True)
    print(f"done: {n_pages} pages, {n_ocr} OCR'd -> {out}")


if __name__ == "__main__":
    main()
