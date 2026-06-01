"""OpenAI-compatible extractor -- run a *local* or third-party model as a second,
decorrelated expert for the ensemble verifier.

Points at any OpenAI-compatible ``/v1`` endpoint, so you can run **Qwen** (or any
open model) locally via Ollama (``http://localhost:11434/v1``) or vLLM
(``http://localhost:8000/v1``) and use it to cross-check the primary (Claude)
extraction. Pairing a different model family is what makes ensemble disagreement a
useful error signal.

This extractor works on the already-parsed text (a different *model*, same modality).
A fully decorrelated *vision* path -- rendering each PDF page to an image and letting
a VLM read pixels -- is the natural next extension and is noted in docs/verification.md.

Requires ``pip install open-… [vlm]`` (the ``openai`` SDK). Config via env:
``OHD_VLM_BASE_URL``, ``OHD_VLM_MODEL``, ``OHD_VLM_API_KEY`` (or constructor args).
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from ..connectors.base import Connector
from ..models import ExtractionRecord, SourceDocument
from .base import Extractor
from .llm import _chunk  # reuse page-aware chunking


def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)  # salvage a JSON object if wrapped in prose
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                return None
    return None


class OpenAICompatExtractor(Extractor):
    name = "vlm"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, max_chunks: int | None = None):
        self.model = model or os.environ.get("OHD_VLM_MODEL", "qwen2.5-vl")
        self.base_url = base_url or os.environ.get("OHD_VLM_BASE_URL", "http://localhost:11434/v1")
        self.api_key = api_key or os.environ.get("OHD_VLM_API_KEY", "EMPTY")
        self.max_chunks = max_chunks

    def _client(self):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                "The VLM/OpenAI-compatible extractor needs the openai SDK: "
                "`pip install open-health-data[vlm]` (or `pip install openai`)."
            ) from exc
        return OpenAI(base_url=self.base_url, api_key=self.api_key)

    def extract(self, text: str, doc: SourceDocument, connector: Connector) -> list[ExtractionRecord]:
        client = self._client()
        schema = json.dumps(connector.extraction_schema())
        system = (
            connector.extraction_instructions()
            + "\n\nReturn ONLY a single JSON object that conforms to this JSON schema:\n"
            + schema
        )
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

        records: list[ExtractionRecord] = []
        for chunk in chunks:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": "Document text (use [[PAGE n]] markers for pages):\n\n" + chunk},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            payload = _extract_json(resp.choices[0].message.content or "")
            if payload:
                records.extend(connector.records_from_payload(payload, doc, dict(provenance_base)))
        return records
