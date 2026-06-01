#!/usr/bin/env python3
"""Render a synthetic EQR fixture (.txt) to a real PDF, so you can exercise the
full PDF -> text -> extract path locally.

    pip install reportlab
    python scripts/make_sample_eqr_pdf.py data/sample/synthetic_eqr_tx_2024.txt

Writes alongside the input with a .pdf extension. This is for testing the PDF
plumbing only; real reports come from state websites.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    src = Path(argv[1])
    out = src.with_suffix(".pdf")
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        print("Install reportlab first: pip install reportlab")
        return 1

    text = src.read_text(encoding="utf-8")
    c = canvas.Canvas(str(out), pagesize=letter)
    width, height = letter
    y = height - 54
    c.setFont("Helvetica", 7)
    for line in text.splitlines():
        if y < 54:
            c.showPage()
            c.setFont("Helvetica", 7)
            y = height - 54
        # keep the full line on one text line so the connector's line parser can
        # read it back; long lines overflow the page but remain in the text layer.
        c.drawString(40, y, line)
        y -= 11
    c.showPage()
    c.save()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
