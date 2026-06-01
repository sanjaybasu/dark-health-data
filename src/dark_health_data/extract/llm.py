"""Claude-based structured extractor -- the "AI scanning" step.

This is what turns heterogeneous, real-world EQR PDFs into the same tidy schema
the rule parser produces for the canonical fixture. It uses Anthropic tool-use
to force schema-valid JSON, and prompt caching on the (large, static) extraction
instructions + tool schema so that re-running across many documents/chunks is
cheap.

Requires ``pip install dark-health-data[llm]`` and ANTHROPIC_API_KEY. If either
is missing we raise a clear error rather than silently degrading -- scientific
output should never be ambiguous about how it was produced.
"""

from __future__ import annotations

from typing import Any

from ..config import settings
from ..connectors.base import Connector
from ..models import ExtractionRecord, SourceDocument
from .base import Extractor

SYSTEM_PREAMBLE = (
    "You extract structured, research-grade data from public health and "
    "healthcare regulatory documents. You are precise and conservative: you only "
    "report values explicitly present in the text, you never fabricate numbers, "
    "and you set a low confidence when the source is ambiguous."
)

# Characters per model call. EQR reports run 40-300 pages; we window the text to
# stay well within context while keeping related tables together.
DEFAULT_CHUNK_CHARS = 7000


def _chunk(text: str, size: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split on page markers when possible, packing pages up to ``size`` chars."""
    if len(text) <= size:
        return [text]
    # pdf.extract_text inserts '\n[[PAGE n]]\n' markers; split on them so a chunk
    # never straddles a page in a way that loses the page number.
    parts = text.split("\n[[PAGE ")
    pages = [parts[0]] + [f"\n[[PAGE {p}" for p in parts[1:]]
    chunks: list[str] = []
    buf = ""
    for page in pages:
        if buf and len(buf) + len(page) > size:
            chunks.append(buf)
            buf = page
        else:
            buf += page
    if buf:
        chunks.append(buf)
    return chunks


class LLMExtractor(Extractor):
    name = "llm"

    def __init__(self, model: str | None = None, max_chunks: int | None = None):
        self.model = model or settings.llm_model
        self.max_chunks = max_chunks

    def _client(self):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The LLM extractor needs the anthropic SDK. Install with "
                "`pip install dark-health-data[llm]`."
            ) from exc
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Set it (see .env.example) or use the "
                "rule extractor for offline runs."
            )
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def extract(
        self, text: str, doc: SourceDocument, connector: Connector
    ) -> list[ExtractionRecord]:
        client = self._client()
        schema = connector.extraction_schema()
        tools = [
            {
                "name": "emit_records",
                "description": "Return all structured records found in this document chunk.",
                "input_schema": schema,
            }
        ]
        system = [
            {"type": "text", "text": SYSTEM_PREAMBLE},
            # The connector's instructions + (implicitly) the tool schema are the
            # large static prefix; caching them makes multi-chunk / multi-doc runs cheap.
            {
                "type": "text",
                "text": connector.extraction_instructions(),
                "cache_control": {"type": "ephemeral"},
            },
        ]
        provenance_base = {
            "source_document_id": doc.document_id,
            "source_url": doc.source_url,
            "method": "llm",
            "model_name": self.model,
            "extractor_version": connector.version,
        }

        chunks = _chunk(text)
        if self.max_chunks:
            chunks = chunks[: self.max_chunks]

        # chunks are independent API calls -> run them concurrently (bounded) so a
        # 200-page report finishes in ~1-2 min instead of sequentially. A chunk that
        # errors (e.g. a rate limit that outlasts SDK retries) yields None and is
        # skipped, so one bad chunk never loses the whole document.
        import os
        from concurrent.futures import ThreadPoolExecutor

        workers = max(1, int(os.environ.get("OHD_LLM_CONCURRENCY", "8")))

        def safe_call(chunk: str):
            try:
                return self._call(client, system, tools, chunk)
            except Exception:
                return None

        records: list[ExtractionRecord] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            payloads = list(pool.map(safe_call, chunks))
        for payload in payloads:
            if payload:
                records.extend(
                    connector.records_from_payload(payload, doc, dict(provenance_base))
                )
        return records

    def _call(self, client, system, tools, chunk: str) -> dict[str, Any] | None:
        resp = client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=system,
            tools=tools,
            tool_choice={"type": "tool", "name": "emit_records"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract every record from the following document text. "
                        "Use the [[PAGE n]] markers to report the page for each row.\n\n"
                        + chunk
                    ),
                }
            ],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_records":
                return block.input
        return None
