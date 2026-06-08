"""Claude-based structured extractors -- the "AI scanning" step.

Two paths, same schema/prompt:
- ``LLMExtractor`` (name ``llm``): synchronous, one document at a time. Used for
  small/interactive/offline-validation runs and as the ensemble's second expert.
- ``BatchLLMExtractor`` (name ``llm_batch``): the Message Batches API path for
  large, latency-tolerant backfills -- ~50% cheaper and off the synchronous
  rate-limit pool. Driven across many documents at the pipeline level
  (``run_dataset_batch``) so all chunks share one batch.

Both force tool use to emit schema-valid JSON and put the static prefix
(tool/JSON schema, rendered before ``system``, plus the extraction instructions)
behind a single cache breakpoint, so re-running across chunks/docs is cheap *if*
the prefix clears the model's minimum cacheable size (Haiku 4.5 = 4,096 tokens;
below that, caching silently no-ops -- batch's discount is the reliable win).

Requires ``pip install dark-health-data[llm]`` and ANTHROPIC_API_KEY. If either
is missing we raise a clear error rather than silently degrading -- scientific
output should never be ambiguous about how it was produced.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Iterable

from ..config import settings
from ..connectors.base import Connector
from ..models import ExtractionRecord, SourceDocument
from .base import Extractor

log = logging.getLogger("dark_health_data.extract.llm")

SYSTEM_PREAMBLE = (
    "You extract structured, research-grade data from public health and "
    "healthcare regulatory documents. You are precise and conservative: you only "
    "report values explicitly present in the text, you never fabricate numbers, "
    "and you set a low confidence when the source is ambiguous."
)

USER_INSTRUCTION = (
    "Extract every record from the following document text. "
    "Use the [[PAGE n]] markers to report the page for each row.\n\n"
)

# Output ceiling. Kept high deliberately: dense rate tables can emit large JSON,
# and a truncated tool_use block loses records. max_tokens only caps output (it is
# not billed when unused), so the only cost of a high cap is OTPM reservation in
# the synchronous path -- acceptable for data completeness.
MAX_OUTPUT_TOKENS = 8192

# Characters per model call. EQR reports run 40-300 pages; we window the text to
# stay well within context while keeping related tables together.
DEFAULT_CHUNK_CHARS = 7000


def _chunk(text: str, size: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split on page markers when possible, packing pages up to ``size`` chars."""
    if len(text) <= size:
        return [text]
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


def _custom_id(document_id: str, chunk_idx: int) -> str:
    """Batch custom_id: must match ^[a-zA-Z0-9_-]{1,64}$ and be unique per chunk."""
    h = hashlib.sha1(document_id.encode("utf-8")).hexdigest()[:16]
    return f"d{h}-c{chunk_idx}"


class LLMExtractor(Extractor):
    name = "llm"
    # The ExtractionMethod recorded in provenance. Distinct from `name`: the batch
    # subclass keeps name="llm_batch" for the registry but records method="llm" -- a
    # batched extraction is the same extraction as the synchronous one (same model,
    # same prompt), just async, and "llm_batch" is not a valid ExtractionMethod enum.
    provenance_method = "llm"

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

    def _prefix(self, connector: Connector) -> tuple[list[dict], list[dict]]:
        """Byte-stable static prefix shared by every request: (system, tools).

        Render order is tools -> system -> messages, so one cache breakpoint on the
        last system block caches the whole tools+system prefix (the JSON schema and
        the instructions). A 1-hour TTL keeps it warm across a long multi-doc run.
        The schema dict is passed through unchanged (connectors return stable, hand
        written dicts) so the cached bytes are identical on every call.
        """
        tools = [
            {
                "name": "emit_records",
                "description": "Return all structured records found in this document chunk.",
                "input_schema": connector.extraction_schema(),
            }
        ]
        system = [
            {"type": "text", "text": SYSTEM_PREAMBLE},
            {
                "type": "text",
                "text": connector.extraction_instructions(),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        return system, tools

    def _provenance_base(self, doc: SourceDocument, connector: Connector) -> dict[str, Any]:
        return {
            "source_document_id": doc.document_id,
            "source_url": doc.source_url,
            "method": self.provenance_method,
            "model_name": self.model,
            "extractor_version": connector.version,
        }

    def extract(
        self, text: str, doc: SourceDocument, connector: Connector
    ) -> list[ExtractionRecord]:
        client = self._client()
        system, tools = self._prefix(connector)
        provenance_base = self._provenance_base(doc, connector)

        chunks = _chunk(text)
        if self.max_chunks:
            chunks = chunks[: self.max_chunks]
        if not chunks:
            return []

        workers = max(1, int(os.environ.get("OHD_LLM_CONCURRENCY", "8")))

        def safe_call(chunk: str):
            return self._call_with_retry(client, system, tools, chunk, doc)

        # Warm the cache: run the first chunk alone so its prefix write lands before
        # the fan-out, which then reads the cached prefix instead of N parallel writes.
        from concurrent.futures import ThreadPoolExecutor

        payloads = [safe_call(chunks[0])]
        if len(chunks) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                payloads.extend(pool.map(safe_call, chunks[1:]))

        records: list[ExtractionRecord] = []
        for payload in payloads:
            if payload:
                records.extend(
                    connector.records_from_payload(payload, doc, dict(provenance_base))
                )
        return records

    def _call_with_retry(self, client, system, tools, chunk, doc) -> dict[str, Any] | None:
        """Call with typed backoff; on permanent failure LOG (never silently drop)."""
        import anthropic

        for attempt in range(5):
            try:
                return self._call(client, system, tools, chunk)
            except anthropic.RateLimitError as exc:
                wait = getattr(exc, "retry_after", None) or min(2 ** attempt, 30)
                log.warning("rate-limited (attempt %d), sleeping %ss", attempt + 1, wait)
                time.sleep(float(wait))
            except anthropic.APIStatusError as exc:
                if 500 <= getattr(exc, "status_code", 0) < 600:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                log.error("non-retryable API error on a chunk of doc %s: %s",
                          doc.document_id, exc)
                return None
            except Exception as exc:  # pragma: no cover - unexpected
                log.error("unexpected error on a chunk of doc %s: %s", doc.document_id, exc)
                return None
        log.error("chunk dropped after retries (%d chars) for doc %s",
                  len(chunk), doc.document_id)
        return None

    def _call(self, client, system, tools, chunk: str) -> dict[str, Any] | None:
        resp = client.messages.create(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            tools=tools,
            tool_choice={"type": "tool", "name": "emit_records"},
            messages=[{"role": "user", "content": USER_INSTRUCTION + chunk}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_records":
                return block.input
        return None


class BatchLLMExtractor(LLMExtractor):
    """Message Batches API path: ~50% cheaper, off the synchronous rate-limit pool.

    Driven across MANY documents by ``pipeline.run_dataset_batch`` so all chunks
    share one batch and one warm cache window. Not used via ``Extractor.extract``
    (which is per-document); the methods below are called by the batch driver.
    """

    name = "llm_batch"

    def build_requests(
        self, items: Iterable[tuple[SourceDocument, str]], connector: Connector
    ) -> tuple[list[dict], dict[str, tuple[str, int]]]:
        """items: (doc, text) pairs. Returns (batch requests, custom_id -> (doc_id, idx))."""
        system, tools = self._prefix(connector)
        requests: list[dict] = []
        id_map: dict[str, tuple[str, int]] = {}
        for doc, text in items:
            chunks = _chunk(text)
            if self.max_chunks:
                chunks = chunks[: self.max_chunks]
            for i, chunk in enumerate(chunks):
                cid = _custom_id(doc.document_id, i)
                id_map[cid] = (doc.document_id, i)
                requests.append({
                    "custom_id": cid,
                    "params": {
                        "model": self.model,
                        "max_tokens": MAX_OUTPUT_TOKENS,
                        "system": system,
                        "tools": tools,
                        "tool_choice": {"type": "tool", "name": "emit_records"},
                        "messages": [{"role": "user", "content": USER_INSTRUCTION + chunk}],
                    },
                })
        return requests, id_map

    def submit(self, client, requests: list[dict]) -> str:
        return client.messages.batches.create(requests=requests).id

    def poll(self, client, batch_id: str) -> str:
        return client.messages.batches.retrieve(batch_id).processing_status

    def collect(
        self, client, batch_id: str, route: dict[str, SourceDocument], connector: Connector
    ) -> tuple[list[ExtractionRecord], list[tuple[str, str]]]:
        """Stream results (out of order) and map each to records via custom_id.

        ``route`` maps custom_id -> SourceDocument (built by the driver from the
        build_requests id_map). Results are matched by custom_id, never by position.
        """
        records: list[ExtractionRecord] = []
        dropped: list[tuple[str, str]] = []
        provby: dict[str, dict] = {}
        for result in client.messages.batches.results(batch_id):
            cid = result.custom_id
            if result.result.type != "succeeded":
                dropped.append((cid, result.result.type))  # errored/expired/canceled: unbilled
                continue
            payload = None
            for block in result.result.message.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "emit_records":
                    payload = block.input
                    break
            if not payload:
                continue
            doc = route.get(cid)
            if doc is None:
                dropped.append((cid, "unmapped"))
                continue
            if cid not in provby:
                provby[cid] = self._provenance_base(doc, connector)
            records.extend(connector.records_from_payload(payload, doc, dict(provby[cid])))
        return records, dropped
