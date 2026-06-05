"""End-to-end + parser tests for the Section 1115 demonstration evaluation connector."""

from dark_health_data.models import SourceDocument, Waiver1115Finding, Waiver1115Recommendation
from dark_health_data.pipeline import run_dataset


def test_waiver_1115_demo_run():
    summary = run_dataset("waiver_1115", extractor_name="rule", verbose=False, write_parquet=False)
    tables = summary["curation"]["tables"]
    assert tables["waiver_1115_findings"] == 4
    assert tables["waiver_1115_recommendations"] == 3
    assert summary["curation"]["n_documents"] == 1
    # all effect_direction values are in the controlled vocab and years are sane -> no hard fails
    assert summary["validation"]["qa_fail"] == 0


def test_waiver_1115_fields():
    from dark_health_data.connectors.waiver_1115 import Waiver1115Connector

    doc = SourceDocument(
        document_id="d", dataset_id="waiver_1115", jurisdiction="NC",
        program="Healthy Opportunities Pilots (synthetic example)", report_year=2024,
    )
    text = open("data/sample/synthetic_waiver_1115_2024.txt", encoding="utf-8").read()
    recs = Waiver1115Connector().parse_rule_based(text, doc)
    findings = [r for r in recs if isinstance(r, Waiver1115Finding)]
    recommendations = [r for r in recs if isinstance(r, Waiver1115Recommendation)]
    assert len(recommendations) == 3

    mtm = next(f for f in findings if f.intervention == "Medically tailored meals")
    assert mtm.domain == "Food/Nutrition"
    assert mtm.effect_direction == "improved"
    assert mtm.value == -12.5
    assert mtm.outcome_measure == "Food insecurity prevalence"
    # three of the four findings are food/nutrition interventions
    assert sum(f.domain == "Food/Nutrition" for f in findings) == 3
