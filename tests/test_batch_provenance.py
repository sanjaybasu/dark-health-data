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


def test_build_requests_dedupes_documents_so_custom_ids_are_unique():
    """Two source URLs resolving to identical content share a document_id; the batch
    API rejects duplicate custom_ids (a hard 400). build_requests must extract each
    unique document once so every custom_id is unique."""
    conn = get_connector("chna")
    text = "Hospital X CHNA\n[[PAGE 1]] need: housing\n[[PAGE 2]] strategy: clinic"
    # same document_id (same content via two different listing URLs)
    a = SourceDocument(document_id="dup", dataset_id="chna", source_url="http://a/chna.pdf")
    b = SourceDocument(document_id="dup", dataset_id="chna", source_url="http://b/chna.pdf")
    reqs, id_map = BatchLLMExtractor(model="m").build_requests([(a, text), (b, text)], conn)
    cids = [r["custom_id"] for r in reqs]
    assert len(cids) == len(set(cids)), f"duplicate custom_ids: {cids}"
    assert len(reqs) >= 1 and len(id_map) == len(reqs)
