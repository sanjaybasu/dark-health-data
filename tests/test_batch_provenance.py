"""Regression: the batch extractor must record a VALID ExtractionMethod.

BatchLLMExtractor.name is "llm_batch" (registry id), but provenance.method must be
"llm" -- a batched extraction is the same extraction as the synchronous one. A prior
bug recorded method="llm_batch", which is not a valid ExtractionMethod enum, so every
batch run crashed in records_from_payload when it built Provenance.
"""
from __future__ import annotations

from dark_health_data.connectors import get_connector
from dark_health_data.extract.llm import BatchLLMExtractor, LLMExtractor
from dark_health_data.models import Provenance, SourceDocument


def test_both_extractors_record_method_llm_and_validate():
    conn = get_connector("eqr")
    doc = SourceDocument(document_id="x", dataset_id="eqr", source_url="http://x/r.pdf")
    for ex in (LLMExtractor(model="m"), BatchLLMExtractor(model="m")):
        base = ex._provenance_base(doc, conn)
        assert base["method"] == "llm", f"{type(ex).__name__} recorded {base['method']!r}"
        Provenance(**base)  # must not raise (method='llm_batch' would)
