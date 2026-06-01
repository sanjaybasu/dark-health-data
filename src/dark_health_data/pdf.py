"""PDF -> text with page markers, plus an optional OCR fallback.

Born-digital PDFs have a text layer we can read directly (fast, exact). Scanned
PDFs (common for older state reports) have none, so we fall back to OCR -- the
same manual step you currently do by hand. We insert ``[[PAGE n]]`` markers so
downstream extractors can attribute every value to a page.

Two text backends are tried in order: ``pdfplumber`` (best layout fidelity) then
``pymupdf`` (self-contained, no system crypto deps). Whichever is installed and
working wins, so the library keeps running across heterogeneous environments.
Heavy deps are imported lazily and are optional extras; the offline demo never
touches this module.
"""

from __future__ import annotations

from pathlib import Path


def _with_marker(page_no: int, body: str) -> str:
    return f"\n[[PAGE {page_no}]]\n{body.strip()}\n"


def _guarded(fn):
    """Run a backend probe, treating ANY failure (including native-extension
    panics that subclass BaseException, as seen with broken `cryptography`
    builds) as "backend unavailable" -- except real interrupts."""
    try:
        return fn()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _pages_pdfplumber(path: Path) -> list[str] | None:
    def run():
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return [(page.extract_text() or "") for page in pdf.pages]

    return _guarded(run)


def _pages_pymupdf(path: Path) -> list[str] | None:
    def run():
        import fitz  # PyMuPDF

        with fitz.open(str(path)) as doc:
            return [page.get_text() for page in doc]

    return _guarded(run)


def _read_pages(path: Path) -> list[str]:
    for backend in (_pages_pdfplumber, _pages_pymupdf):
        pages = backend(path)
        if pages is not None:
            return pages
    raise RuntimeError(
        "No working PDF backend. Install one with `pip install dark-health-data[pdf]` "
        "(pdfplumber) or `pip install pymupdf`."
    )


def extract_text(path: str | Path, *, ocr: bool = False, ocr_min_chars: int = 20) -> str:
    """Extract text from a PDF, inserting ``[[PAGE n]]`` markers.

    If a page yields almost no text and ``ocr=True``, OCR that page.
    """
    path = Path(path)
    if path.suffix.lower() == ".txt":
        # Convenience for fixtures/tests: treat .txt as a single-"page" document.
        return _with_marker(1, path.read_text(encoding="utf-8"))

    pages = _read_pages(path)
    out: list[str] = []
    for i, body in enumerate(pages, start=1):
        if ocr and len(body.strip()) < ocr_min_chars:
            body = _ocr_page(path, i) or body
        out.append(_with_marker(i, body))
    return "".join(out)


def _ocr_page(path: Path, page_no: int) -> str:
    """OCR a single page. Requires the [ocr] extra (pdf2image + pytesseract) and tesseract."""
    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "OCR needs the [ocr] extra (pdf2image, pytesseract) and a tesseract install."
        ) from exc
    images = convert_from_path(str(path), first_page=page_no, last_page=page_no)
    return "\n".join(pytesseract.image_to_string(img) for img in images)
