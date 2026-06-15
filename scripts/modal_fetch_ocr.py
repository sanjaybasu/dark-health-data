"""Modal-offloaded fetch + OCR for Hidden Health Data (esp. scanned nursing 2567s).

Each call handles ONE PDF: download (with the same TLS fallback as fetch.py) + page-marked
text extraction + OCR fallback for scanned pages -- mirroring pdf.extract_text exactly so the
output is byte-compatible with the connectors' `[[PAGE n]]` chunking. Fanned out with .map()
so (a) the local machine's network/CPU is never the bottleneck (the saturation that timed out
local runs), (b) OCR runs in parallel, and (c) no single function approaches Modal's 12h cap.

Produces data/cache/modal_text/<dataset>.jsonl, which run_dataset_batch consumes (skipping
local fetch+OCR). Deploys a NEW app 'dhd-fetch-ocr'; does not touch other Modal apps.

Usage:  modal run scripts/modal_fetch_ocr.py --dataset nursing_home_2567 [--limit N]
"""
from __future__ import annotations

import modal

app = modal.App("dhd-fetch-ocr")
image = (
    modal.Image.debian_slim()
    .apt_install("tesseract-ocr", "poppler-utils")
    .pip_install("pdfplumber", "pymupdf", "pdf2image", "pytesseract", "requests", "urllib3")
)

_UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "dark-health-data/0.4 (+https://github.com/sanjaybasu/dark-health-data; research)",
    "curl/8.4.0",
]


def _download(url: str) -> bytes:
    import ssl

    import requests
    import urllib3
    from requests.adapters import HTTPAdapter
    from urllib3.util.ssl_ import create_urllib3_context

    last = None
    for ua in _UA:
        try:
            r = requests.get(url, headers={"User-Agent": ua, "Accept": "application/pdf,*/*;q=0.8"}, timeout=120)
            r.raise_for_status()
            return r.content
        except Exception as exc:  # noqa: BLE001
            last = exc
    if not isinstance(last, requests.exceptions.SSLError):
        raise last  # type: ignore[misc]
    # incomplete chain or legacy renegotiation -> verify-off + OP_LEGACY_SERVER_CONNECT
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    ctx = create_urllib3_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    class _Legacy(HTTPAdapter):
        def init_poolmanager(self, *a, **k):
            k["ssl_context"] = ctx
            return super().init_poolmanager(*a, **k)

    s = requests.Session()
    s.mount("https://", _Legacy())
    r = s.get(url, headers={"User-Agent": _UA[0], "Accept": "application/pdf,*/*;q=0.8"}, timeout=120, verify=False)
    r.raise_for_status()
    return r.content


def _extract(raw: bytes) -> tuple[str, int, bool]:
    """Mirror pdf.extract_text: page texts via PyMuPDF (pdfplumber fallback), OCR pages
    whose text layer is < 20 chars. Returns (page-marked text, n_pages, scanned?)."""
    import io

    pages: list[str] = []
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=raw, filetype="pdf") as doc:
            pages = [p.get_text() for p in doc]
    except Exception:  # noqa: BLE001
        import pdfplumber

        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]

    need = [i for i, b in enumerate(pages) if len((b or "").strip()) < 20]
    scanned = bool(need)
    if need:
        from pdf2image import convert_from_bytes
        import pytesseract

        for i in need:
            try:
                imgs = convert_from_bytes(raw, first_page=i + 1, last_page=i + 1, dpi=200)
                pages[i] = "\n".join(pytesseract.image_to_string(im) for im in imgs)
            except Exception:  # noqa: BLE001 - keep the (empty) text-layer page on OCR failure
                pass
    out = "".join(f"\n[[PAGE {i + 1}]]\n{(b or '').strip()}\n" for i, b in enumerate(pages))
    return out, len(pages), scanned


@app.function(image=image, timeout=1800, retries=2, max_containers=50)
def fetch_ocr(item: dict) -> dict:
    import hashlib

    url = item["url"]
    res: dict = {"url": url, "ok": False}
    try:
        raw = _download(url)
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"fetch: {type(exc).__name__}: {str(exc)[:150]}"
        return res
    res["document_id"] = hashlib.sha256(raw).hexdigest()
    try:
        text, npages, scanned = _extract(raw)
        res.update(ok=True, text=text, n_pages=npages, scanned=scanned)
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"extract: {type(exc).__name__}: {str(exc)[:150]}"
    return res


@app.local_entrypoint()
def main(dataset: str, limit: int = 0):
    import json
    from pathlib import Path

    import yaml

    src = yaml.safe_load(open(f"registry/sources_{dataset}.yaml"))["sources"]
    items = []
    for s in src:
        for rep in s.get("reports", []):
            if rep.get("url"):
                items.append({"url": rep["url"]})
    if limit:
        items = items[:limit]
    print(f"dispatching {len(items)} PDFs to Modal (dhd-fetch-ocr)...")
    results = list(fetch_ocr.map(items))
    out = Path(f"data/cache/modal_text/{dataset}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")
    ok = sum(1 for r in results if r.get("ok"))
    sc = sum(1 for r in results if r.get("scanned"))
    print(f"OK {ok}/{len(items)} | scanned {sc} | failed {len(items) - ok} -> {out}")
