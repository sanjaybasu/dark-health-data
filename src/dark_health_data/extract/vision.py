"""Claude *vision* extractor -- reads data encoded in charts/figures, not just text.

The text extractors (``llm``/``llm_batch``) read a PDF's text layer. That layer is
silent about values that live *only* in a figure: a line chart's plotted points, a
bar's height, a labelled trend marker. Human validation of the EQR corpus found this
is a real, concentrated failure mode -- a state that presents maternal-morbidity rates
as a chart (rather than a table) gets those rows mis-attributed or missed, because the
scraper sees stray vector-text fragments with no idea which series/year they belong to.

This extractor renders *figure-dense* pages to images and asks a vision-capable Claude
model to read them, reusing the connector's own extraction schema/instructions so the
output is identical in shape to the text path. It is deliberately **targeted**: only
pages that look like figures (vector/raster graphics with sparse tabular numerics) are
rendered, so a full report costs a few page-images, not hundreds.

Design choices that matter for trust:
* It does **not** set ``provenance.source_span`` -- a chart value is not a text span, so
  the grounding verifier correctly stays neutral rather than false-failing a good read.
  Confidence for these records comes from the ensemble (text-vs-vision agreement) and the
  symbolic constraints (num<=den, percent-in-range, rate==num/den).
* ``method`` is recorded as ``vision`` and ``model_name`` as the VLM id, so every
  chart-derived value is auditable and separable in analysis.

Requires ``pip install dark-health-data[llm]`` (anthropic) + a PDF backend (pymupdf) and
ANTHROPIC_API_KEY. Heavy deps are imported lazily; the offline demo never touches this.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Optional

from ..config import settings
from ..connectors.base import Connector
from ..models import ExtractionMethod, SourceDocument
from .base import Extractor

log = logging.getLogger("dark_health_data.extract.vision")

VISION_INSTRUCTION_SUFFIX = (
    "\n\nYou are reading a RENDERED IMAGE of a single report page that contains one or "
    "more figures (line charts, bar charts, plotted trends). Read values directly from "
    "the figure: follow each series/line to its data label or gridline, and attribute "
    "every value to the correct series, category/cohort, and year shown on the axes and "
    "legend. Report ONLY values you can actually read from the figure or its data labels "
    "-- never interpolate an unlabelled point or guess a hidden value. Set a lower "
    "confidence when a label is small, overlapping, or partially occluded."
)

MAX_OUTPUT_TOKENS = 4096


class ClaudeVisionExtractor(Extractor):
    """Render figure-dense pages to images and extract with a vision-capable Claude model."""

    name = "vision"
    provenance_method = ExtractionMethod.VISION

    def __init__(
        self,
        model: str | None = None,
        *,
        dpi: int = 200,
        max_pages: int | None = 12,
        escalate_model: str | None = "claude-sonnet-4-6",
        pages: Optional[list[int]] = None,
    ):
        # default to the cheapest vision-capable model; the caller can override
        self.model = model or os.environ.get("OHD_VLM_MODEL") or "claude-haiku-4-5"
        self.dpi = dpi
        self.max_pages = max_pages
        self.escalate_model = escalate_model
        self.pages = pages  # explicit 1-indexed pages to read; else auto-detect figures

    # ----- dependencies -----
    def _client(self):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                "The vision extractor needs the anthropic SDK. Install with "
                "`pip install dark-health-data[llm]`."
            ) from exc
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Set it (see .env.example) or use the "
                "rule extractor for offline runs."
            )
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # ----- figure detection -----
    @staticmethod
    def figure_pages(pdf_path: str, *, max_pages: int | None = 12) -> list[int]:
        """1-indexed pages that look like figures: graphics present, tabular numerics sparse.

        A page qualifies if it has raster images or a non-trivial number of vector
        drawings (lines/curves -- the body of a chart) AND its text layer is not already
        a dense numeric table (which the text extractor handles well). Returns at most
        ``max_pages`` pages, the most graphics-heavy first, to bound cost.
        """
        import fitz  # PyMuPDF

        scored: list[tuple[float, int]] = []
        with fitz.open(pdf_path) as doc:
            for i, page in enumerate(doc, start=1):
                text = page.get_text() or ""
                n_images = len(page.get_images())
                n_draw = len(page.get_drawings())
                digits = sum(c.isdigit() for c in text)
                # a real rate TABLE is digit-dense; a chart page is graphics-dense with
                # comparatively little digit text. Require graphics, and skip pages whose
                # text already reads like a big table.
                graphics = n_images > 0 or n_draw >= 12
                table_like = digits > 400
                if graphics and not table_like:
                    scored.append((n_draw + 50 * n_images, i))
        scored.sort(reverse=True)
        pages = [i for _, i in scored]
        return pages[:max_pages] if max_pages else pages

    def _render(self, pdf_path: str, page_no: int) -> str:
        """Render a 1-indexed page to a base64 PNG."""
        import fitz

        with fitz.open(pdf_path) as doc:
            pix = doc[page_no - 1].get_pixmap(dpi=self.dpi)
            return base64.standard_b64encode(pix.tobytes("png")).decode()

    # ----- extraction -----
    def extract(
        self, text: str, doc: SourceDocument, connector: Connector
    ) -> list[Any]:
        if not doc.local_path:
            log.warning("vision extractor needs a local PDF path; skipping %s", doc.document_id)
            return []
        pages = self.pages or self.figure_pages(doc.local_path, max_pages=self.max_pages)
        if not pages:
            return []
        client = self._client()
        schema = connector.extraction_schema()
        instructions = connector.extraction_instructions() + VISION_INSTRUCTION_SUFFIX
        tool = {"name": "emit_records", "description": "Return all records read from this figure page.",
                "input_schema": schema}

        records: list[Any] = []
        for page_no in pages:
            payload = self._read_page(client, doc.local_path, page_no, instructions, tool)
            if not payload:
                continue
            prov_base = {
                "source_document_id": doc.document_id,
                "source_url": doc.source_url,
                "method": self.provenance_method,
                "model_name": self.model,
                "extractor_version": connector.version,
            }
            # stamp the rendered page so provenance points at the figure, overriding any
            # page the model guessed (it only saw one page image).
            for key in ("quality_measures", "performance_improvement_projects",
                        "compliance_findings"):
                for item in payload.get(key, []) or []:
                    if isinstance(item, dict):
                        item["page"] = page_no
            records.extend(connector.records_from_payload(payload, doc, dict(prov_base)))
        return records

    def _read_page(self, client, pdf_path, page_no, instructions, tool) -> dict | None:
        """Read one page image; escalate Haiku->Sonnet once if the cheap model returns nothing."""
        img = self._render(pdf_path, page_no)
        for model in [self.model] + ([self.escalate_model] if self.escalate_model else []):
            payload = self._call_with_retry(client, model, img, instructions, tool, page_no)
            if payload and any(payload.get(k) for k in
                               ("quality_measures", "performance_improvement_projects",
                                "compliance_findings")):
                return payload
        return payload  # may be empty/None: a genuinely value-free figure page

    #: retry attempts per page. Vision (image) requests are token-heavy and hit ITPM
    #: limits under any concurrency; a corpus pass must not silently drop a figure page,
    #: so back off patiently rather than give up.
    MAX_ATTEMPTS = 9

    def _call_with_retry(self, client, model, img, instructions, tool, page_no) -> dict | None:
        import anthropic

        for attempt in range(self.MAX_ATTEMPTS):
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    system=instructions,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": "emit_records"},
                    messages=[{"role": "user", "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": "image/png", "data": img}},
                        {"type": "text", "text": "Read every value from the figure(s) on this page."},
                    ]}],
                )
                for block in resp.content:
                    if getattr(block, "type", None) == "tool_use" and block.name == "emit_records":
                        return block.input
                return None
            except anthropic.RateLimitError as exc:
                wait = getattr(exc, "retry_after", None) or min(2 ** attempt, 60)
                log.warning("vision rate-limited (attempt %d), sleeping %ss", attempt + 1, wait)
                time.sleep(float(wait))
            except anthropic.APIStatusError as exc:
                if 500 <= getattr(exc, "status_code", 0) < 600:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                log.error("non-retryable vision error on page %d: %s", page_no, exc)
                return None
            except Exception as exc:  # pragma: no cover - unexpected
                log.error("unexpected vision error on page %d: %s", page_no, exc)
                return None
        log.error("vision page %d dropped after retries", page_no)
        return None
