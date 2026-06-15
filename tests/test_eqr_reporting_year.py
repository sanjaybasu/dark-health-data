"""reporting_year is the MEASUREMENT year, not the publication year.

The connector must: (a) use the model's measurement year when present, (b) flag a record
whose year was defaulted to the report's publication year, and (c) flag years that
post-date the report (impossible) or sit implausibly far before it (parse errors).
"""
from __future__ import annotations

from dark_health_data.connectors.eqr import EQRConnector
from dark_health_data.models import QAStatus, SourceDocument

_PROV = dict(source_document_id="d", source_url=None, method="llm",
             model_name="claude-haiku-4-5", extractor_version="0.1.0")


def _measures(payload):
    doc = SourceDocument(document_id="d", dataset_id="eqr", jurisdiction="TX", report_year=2024)
    return EQRConnector().records_from_payload(payload, doc, dict(_PROV))


def test_measurement_year_used_and_unflagged():
    r = _measures({"quality_measures": [
        {"plan_name": "P", "measure_name": "CBP", "reporting_year": 2023, "rate": 55}]})[0]
    assert r.reporting_year == 2023
    assert not any("reporting_year" in f for f in r.qa_flags)


def test_defaulted_year_is_flagged():
    r = _measures({"quality_measures": [
        {"plan_name": "P", "measure_name": "CBP", "rate": 55}]})[0]  # no reporting_year
    assert r.reporting_year == 2024  # fell back to publication year
    assert any("defaulted to publication year" in f for f in r.qa_flags)
    assert r.qa_status == QAStatus.WARN


def test_future_year_is_flagged():
    r = _measures({"quality_measures": [
        {"plan_name": "P", "measure_name": "CBP", "reporting_year": 2025, "rate": 55}]})[0]
    assert any("after report year" in f for f in r.qa_flags)
