"""End-to-end test of the offline pipeline on the shipped synthetic fixtures."""

from pathlib import Path

from dark_health_data.pipeline import run_dataset


def test_full_eqr_demo_run():
    summary = run_dataset("eqr", extractor_name="rule", verbose=False, write_parquet=False)

    curation = summary["curation"]
    # TX: 7 measures, OH: 6 measures (incl. one intentional duplicate) = 13
    assert curation["tables"]["eqr_quality_measures"] == 13
    assert curation["tables"]["eqr_performance_improvement_projects"] == 3
    assert curation["tables"]["eqr_compliance_findings"] == 5
    assert curation["n_documents"] == 2

    val = summary["validation"]
    # one FAIL (numerator>denominator in TX Breast Cancer Screening)
    assert val["qa_fail"] == 1
    # WARNs: TX postpartum rate/den disagreement + 2 duplicated OH FUH rows
    assert val["qa_warn"] == 3

    # verification layer: trust scores + review gate
    assert val["mean_trust"] is not None and 0.0 < val["mean_trust"] <= 1.0
    assert val["min_trust"] < 0.2  # the impossible measure is driven to ~0 trust
    # every non-PASS row (1 fail + 3 warn) is routed to human review
    assert val["review_recommended"] == 4
    assert val["auto_acceptable"] == val["n_records"] - 4

    out_dir = Path(summary["out_dir"])
    for name in [
        "eqr_quality_measures.csv",
        "documents.csv",
        "records.jsonl",
        "DATA_DICTIONARY.md",
        "DATASET_CARD.md",
        "croissant.json",
    ]:
        assert (out_dir / name).exists(), f"missing output: {name}"
