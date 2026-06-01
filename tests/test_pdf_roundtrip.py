"""Optional PDF-path test: only runs when a PDF backend + reportlab are present.

Verifies the born-digital path: text fixture -> rendered PDF -> extract_text ->
rule parser yields the same records. Skipped in the minimal offline install.
"""

import pytest

from dark_health_data import pdf
from dark_health_data.connectors.eqr import EQRConnector
from dark_health_data.models import EQRQualityMeasure, SourceDocument

# needs reportlab to render the fixture into a PDF
pytest.importorskip("reportlab")


def test_pdf_roundtrip(tmp_path):
    src = "data/sample/synthetic_eqr_tx_2024.txt"
    out = tmp_path / "tx.pdf"

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), pagesize=letter)
    _, height = letter
    y = height - 54
    c.setFont("Helvetica", 7)
    for line in open(src, encoding="utf-8").read().splitlines():
        if y < 54:
            c.showPage()
            c.setFont("Helvetica", 7)
            y = height - 54
        c.drawString(40, y, line)
        y -= 11
    c.showPage()
    c.save()

    try:
        text = pdf.extract_text(out)
    except RuntimeError:
        pytest.skip("no working PDF backend installed")

    doc = SourceDocument(
        document_id="t", dataset_id="eqr", jurisdiction="TX",
        program="Medicaid managed care", report_year=2024,
    )
    measures = [r for r in EQRConnector().parse_rule_based(text, doc) if isinstance(r, EQRQualityMeasure)]
    assert len(measures) == 7
    assert measures[0].numerator == 1164
