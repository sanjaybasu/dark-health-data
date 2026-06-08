"""Regression tests: records_from_payload must survive messy real LLM output.

Real extractions returned (a) a stray string where an object was expected and
(b) report_year='<UNKNOWN>'. The connectors must skip the bad item and coerce the
year rather than crash the whole document.
"""
from __future__ import annotations

from dark_health_data.connectors.chna import CHNAConnector
from dark_health_data.connectors.mmrc import MMRCConnector
from dark_health_data.models import SourceDocument

_PROV = dict(source_document_id="d", source_url=None, method="llm",
             model_name="claude-haiku-4-5-20251001", extractor_version="0.1.0")


def test_chna_payload_robustness():
    doc = SourceDocument(document_id="d", dataset_id="chna", jurisdiction="CA", report_year=2023)
    payload = {
        "report_year": "<UNKNOWN>",  # must coerce, not crash int field
        "identified_needs": ["junk-string", {"need": "Mental health", "is_priority": True}],
        "implementation_strategies": ["junk", {"strategy": "Open a clinic"}],
    }
    recs = CHNAConnector().records_from_payload(payload, doc, dict(_PROV))
    assert len(recs) == 2  # the two stray strings are skipped
    assert all(r.report_year == 2023 for r in recs)  # fell back to doc year


def test_mmrc_payload_robustness():
    doc = SourceDocument(document_id="d", dataset_id="mmrc", jurisdiction="MS", report_year=2024)
    payload = {
        "report_year": "<UNKNOWN>",
        "findings": ["junk", {"population_group": "Overall", "pct_preventable": 80}],
        "recommendations": ["junk", {"recommendation": "Extend postpartum coverage"}],
    }
    recs = MMRCConnector().records_from_payload(payload, doc, dict(_PROV))
    assert len(recs) == 2
