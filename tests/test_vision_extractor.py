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

    # page 1: a line "chart" -- captioned, with strokes
    p1 = doc.new_page()
    for i in range(40):
        y = 100 + i * 3
        p1.draw_line(fitz.Point(72, y), fitz.Point(500, y - (i % 7)))
    p1.insert_text(fitz.Point(72, 72), "Figure 1. Trend over time")

    # page 2: a text-only "table" -- digit-dense, no caption, no graphics
    p2 = doc.new_page()
    rows = "\n".join(f"Plan {n}: 12,345 / 67,890 = {n}.{n}%" for n in range(40))
    p2.insert_text(fitz.Point(72, 100), rows)

    # page 3: the REGRESSION -- a ruled table. Hundreds of border strokes (which the old
    # detector mistook for a chart) but a "Table" caption and no "Figure" caption.
    p3 = doc.new_page()
    for r in range(20):
        y = 90 + r * 18
        p3.draw_line(fitz.Point(72, y), fitz.Point(520, y))      # row rules
    for c in range(8):
        x = 72 + c * 56
        p3.draw_line(fitz.Point(x, 90), fitz.Point(x, 450))      # column rules
    p3.insert_text(fitz.Point(72, 72), "Table 5. Performance measure rates by plan")

    # page 4: a BAR chart -- only axis-aligned rectangles/lines (no diagonal strokes), but a
    # "Figure" caption. Caption-detection must catch it where stroke-geometry would not.
    p4 = doc.new_page()
    p4.draw_line(fitz.Point(80, 100), fitz.Point(80, 400))       # y-axis
    p4.draw_line(fitz.Point(80, 400), fitz.Point(520, 400))      # x-axis
    for g in range(5):                                           # gridlines
        y = 100 + g * 60
        p4.draw_line(fitz.Point(80, y), fitz.Point(520, y))
    for b in range(8):                                           # bars
        p4.draw_rect(fitz.Rect(95 + b * 52, 400 - (b + 1) * 30, 135 + b * 52, 400))
    p4.insert_text(fitz.Point(72, 72), "Figure 2. Rates by category")

    doc.save(str(out))
    doc.close()

    pages = ClaudeVisionExtractor.figure_pages(str(out))
    assert 1 in pages          # captioned line chart -> detected
    assert 2 not in pages      # text-only table -> left to the text extractor
    assert 3 not in pages      # ruled table (many strokes, Table caption) -> NOT a figure
    assert 4 in pages          # captioned bar chart (no diagonal strokes) -> detected
