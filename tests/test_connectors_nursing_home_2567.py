"""End-to-end + parser tests for the nursing-home CMS-2567 connector."""

from dark_health_data.models import (
    NursingHomeDeficiency,
    NursingHomePlanOfCorrection,
    SourceDocument,
)
from dark_health_data.pipeline import run_dataset


def test_nursing_home_2567_demo_run():
    summary = run_dataset("nursing_home_2567", extractor_name="rule", verbose=False, write_parquet=False)
    tables = summary["curation"]["tables"]
    assert tables["nursing_home_deficiencies"] == 4
    assert tables["nursing_home_plans_of_correction"] == 3
    assert summary["curation"]["n_documents"] == 1
    # all scope/severity letters are valid A-L and the year is sane -> no hard fails
    assert summary["validation"]["qa_fail"] == 0


def test_nursing_home_2567_fields():
    from dark_health_data.connectors.nursing_home_2567 import NursingHome2567Connector

    doc = SourceDocument(
        document_id="d", dataset_id="nursing_home_2567", jurisdiction="OH",
        publisher="Maplewood Care Center (synthetic example)", report_year=2024,
    )
    text = open("data/sample/synthetic_nursing_home_2567_2024.txt", encoding="utf-8").read()
    recs = NursingHome2567Connector().parse_rule_based(text, doc)
    deficiencies = [r for r in recs if isinstance(r, NursingHomeDeficiency)]
    pocs = [r for r in recs if isinstance(r, NursingHomePlanOfCorrection)]
    assert len(pocs) == 3

    fall = next(d for d in deficiencies if d.ftag == "F689")
    assert fall.scope_severity == "G"
    assert fall.state == "OH"
    assert "fall" in (fall.deficiency_description or "").lower()
    # CCN carried from the document header through the parser
    assert fall.ccn == "365999"
