"""End-to-end + parser tests for the CHNA and MMRC connectors."""

from dark_health_data.models import (
    CHNAIdentifiedNeed,
    MMRCFinding,
    MMRCRecommendation,
)
from dark_health_data.pipeline import run_dataset


def test_chna_demo_run():
    summary = run_dataset("chna", extractor_name="rule", verbose=False, write_parquet=False)
    tables = summary["curation"]["tables"]
    assert tables["chna_identified_needs"] == 5
    assert tables["chna_implementation_strategies"] == 4
    assert summary["curation"]["n_documents"] == 1
    assert summary["validation"]["qa_fail"] == 0


def test_chna_fields():
    from dark_health_data.connectors.chna import CHNAConnector
    from dark_health_data.models import SourceDocument

    doc = SourceDocument(
        document_id="d", dataset_id="chna", publisher="Riverside", jurisdiction="CA", report_year=2023
    )
    text = open("data/sample/synthetic_chna_2023.txt", encoding="utf-8").read()
    needs = [r for r in CHNAConnector().parse_rule_based(text, doc) if isinstance(r, CHNAIdentifiedNeed)]
    top = next(n for n in needs if n.priority_rank == 1)
    assert top.domain == "Mental health"
    assert top.is_priority is True
    # the maternal/child need is marked not-priority
    assert any(n.is_priority is False for n in needs)


def test_mmrc_demo_run():
    summary = run_dataset("mmrc", extractor_name="rule", verbose=False, write_parquet=False)
    tables = summary["curation"]["tables"]
    assert tables["mmrc_findings"] == 4
    assert tables["mmrc_recommendations"] == 4
    # all pct_preventable values are within range -> no failures
    assert summary["validation"]["qa_fail"] == 0


def test_mmrc_fields_and_disparity():
    from dark_health_data.connectors.mmrc import MMRCConnector
    from dark_health_data.models import SourceDocument

    doc = SourceDocument(document_id="d", dataset_id="mmrc", jurisdiction="CA", report_year=2023)
    text = open("data/sample/synthetic_mmrc_2023.txt", encoding="utf-8").read()
    recs = MMRCConnector().parse_rule_based(text, doc)
    findings = {f.population_group: f for f in recs if isinstance(f, MMRCFinding)}
    assert findings["Overall"].pregnancy_related_mortality_ratio == 22.4
    assert findings["Overall"].pct_preventable == 84.0
    # the synthetic data encodes the well-documented disparity (Black ratio highest)
    assert findings["Black non-Hispanic"].pregnancy_related_mortality_ratio == 51.2
    assert sum(isinstance(r, MMRCRecommendation) for r in recs) == 4
