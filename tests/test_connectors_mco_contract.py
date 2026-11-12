"""Tests for the Medicaid managed care contract connector."""

from dark_health_data.connectors import get_connector
from dark_health_data.models import MCOContractProvision, SourceDocument
from dark_health_data.pipeline import run_dataset

DOC = SourceDocument(document_id="d", dataset_id="mco_contract", jurisdiction="WA",
                     title="Apple Health IMC Contract 2025", program="model contract",
                     report_year=2025)
BASE = {"source_document_id": "d", "method": "llm"}


def test_mco_contract_demo_run():
    summary = run_dataset("mco_contract", extractor_name="rule", verbose=False,
                          write_parquet=False)
    assert summary["curation"]["tables"]["mco_contract_provisions"] == 2
    assert summary["validation"]["qa_fail"] == 0


def test_payload_maps_quote_to_source_span():
    conn = get_connector("mco_contract")
    payload = {"provisions": [{
        "section": "4.3", "provision_category": "health risk assessment and screening",
        "requirement": "Screen new members within 90 days.", "numeric_value": "90",
        "numeric_unit": "Days", "obligation": "must", "page": 41,
        "quote": "within ninety (90) days of enrollment",
    }]}
    (r,) = conn.records_from_payload(payload, DOC, BASE)
    assert isinstance(r, MCOContractProvision)
    assert (r.state, r.contract_year, r.document_type) == ("WA", 2025, "model contract")
    assert (r.numeric_value, r.numeric_unit) == (90.0, "days")
    assert r.provenance.source_span == "within ninety (90) days of enrollment"
    assert r.provenance.page_start == 41


def test_rules_flag_problems():
    conn = get_connector("mco_contract")
    rules = {c.name: c for c in conn.constraints() if hasattr(c, "fn")}
    ok = conn.records_from_payload({"provisions": [{
        "provision_category": "community health workers", "requirement": "x",
        "quote": "The Contractor shall employ CHWs."}]}, DOC, BASE)[0]
    assert rules["has_quote"].fn(ok)[0] is True
    assert rules["known_category"].fn(ok)[0] is True
    no_quote = ok.model_copy(update={"provenance": ok.provenance.model_copy(
        update={"source_span": None})})
    assert rules["has_quote"].fn(no_quote)[0] is False
    odd = ok.model_copy(update={"numeric_unit": "fortnights"})
    assert rules["known_unit"].fn(odd)[0] is False
    pct = MCOContractProvision(provenance=BASE, state="WA", numeric_value=150,
                               numeric_unit="percent")
    assert any("percent outside" in f for f in pct.qa_flags)
