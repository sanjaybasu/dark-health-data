"""Regression tests: records_from_payload must survive messy real LLM output.

Real extractions returned (a) a stray string where an object was expected and
(b) report_year='<UNKNOWN>'. The connectors must skip the bad item and coerce the
year rather than crash the whole document.
"""
from __future__ import annotations

from dark_health_data.connectors.chna import CHNAConnector
from dark_health_data.connectors.mmrc import MMRCConnector
from dark_health_data.connectors.nursing_home_2567 import NursingHome2567Connector
from dark_health_data.connectors.waiver_1115 import Waiver1115Connector
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


def test_waiver_payload_robustness():
    doc = SourceDocument(document_id="d", dataset_id="waiver_1115", jurisdiction="NJ", report_year=2022)
    payload = {
        "report_year": "<UNKNOWN>",  # must coerce, not crash int field
        "findings": ["junk", {"intervention": "Food box", "domain": "nutrition"}],
        "recommendations": ["junk", {"recommendation": "Extend HRSN services"}],
    }
    recs = Waiver1115Connector().records_from_payload(payload, doc, dict(_PROV))
    assert len(recs) == 2
    assert all(r.report_year == 2022 for r in recs)  # fell back to doc year


def test_nursing_2567_payload_robustness():
    doc = SourceDocument(document_id="d", dataset_id="nursing_home_2567", jurisdiction="FL", report_year=2024)
    payload = {
        "report_year": "<UNKNOWN>",
        "deficiencies": ["junk", {"ftag": "F684", "deficiency_description": "Quality of care"}],
        # an explicit null `correction` (key present, value None) must not crash .strip()
        "plans_of_correction": ["junk", {"correction": "Retrain staff"}, {"ftag": "F880", "correction": None}],
    }
    recs = NursingHome2567Connector().records_from_payload(payload, doc, dict(_PROV))
    assert len(recs) == 3  # 1 deficiency + 2 plans (stray strings skipped)
    assert all(r.report_year == 2024 for r in recs)
    pocs = [r for r in recs if getattr(r, "correction", None) is not None]
    assert any(r.correction == "" for r in pocs)  # null coerced to empty, not a crash
