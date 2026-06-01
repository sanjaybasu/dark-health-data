"""Offline test of the ensemble path: run a second extractor and feed the ensemble
verifier. Uses rule+rule (identical) to exercise the wiring without network/models."""

from dark_health_data.pipeline import run_dataset


def test_ensemble_wiring_offline():
    summary = run_dataset(
        "eqr",
        extractor_name="rule",
        second_extractor_name="rule",
        verbose=False,
        write_parquet=False,
    )
    val = summary["validation"]
    # identical extractors => the 2nd corroborates the 1st => no omissions
    assert val["ensemble_omissions"] == 0
    # the impossible measure still fails on logic regardless of corroboration
    assert val["qa_fail"] == 1
    assert val["review_recommended"] >= 1
    assert val["mean_trust"] is not None
