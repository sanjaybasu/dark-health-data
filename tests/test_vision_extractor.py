"""Vision extractor: offline-safe unit tests (no network, no API key).

Covers the parts that don't need Claude: the registry wiring, the figure-page
detector (the cost-bounding gate), and the no-local-path guard. The actual vision
call is exercised separately in the re-validation harness, not in CI.
"""

import pytest

from dark_health_data.extract import get_extractor
from dark_health_data.extract.vision import ClaudeVisionExtractor
from dark_health_data.models import ExtractionMethod, SourceDocument


def test_registry_returns_vision_extractor():
    ex = get_extractor("vision")
    assert isinstance(ex, ClaudeVisionExtractor)
    assert ex.name == "vision"
    assert ex.provenance_method == ExtractionMethod.VISION


def test_extract_without_local_path_is_safe():
    """No PDF on disk -> no API call, empty result (never crashes a run)."""
    ex = get_extractor("vision")
    doc = SourceDocument(document_id="x", dataset_id="eqr", local_path=None)
    assert ex.extract("ignored text", doc, connector=None) == []


def test_figure_pages_picks_charts_over_tables(tmp_path):
    """A vector-drawing page should be flagged; a digit-dense table page should not."""
    fitz = pytest.importorskip("fitz")

    out = tmp_path / "doc.pdf"
    doc = fitz.open()

    # page 1: a "chart" -- many vector strokes, little digit text
    p1 = doc.new_page()
    for i in range(40):
        y = 100 + i * 3
        p1.draw_line(fitz.Point(72, y), fitz.Point(500, y - (i % 7)))
    p1.insert_text(fitz.Point(72, 72), "Figure 1. Trend over time")

    # page 2: a "table" -- digit-dense text, no graphics
    p2 = doc.new_page()
    rows = "\n".join(f"Plan {n}: 12,345 / 67,890 = {n}.{n}%" for n in range(40))
    p2.insert_text(fitz.Point(72, 100), rows)

    doc.save(str(out))
    doc.close()

    pages = ClaudeVisionExtractor.figure_pages(str(out))
    assert 1 in pages          # the chart page is detected
    assert 2 not in pages      # the dense numeric table is left to the text extractor
