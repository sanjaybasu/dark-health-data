from dark_health_data.connectors.eqr import EQRConnector
from dark_health_data.models import (
    EQRComplianceFinding,
    EQRPerformanceImprovementProject,
    EQRQualityMeasure,
    MeasureSteward,
    SourceDocument,
)


def _doc():
    return SourceDocument(
        document_id="deadbeef",
        dataset_id="eqr",
        jurisdiction="TX",
        program="Medicaid managed care",
        report_year=2024,
    )


FIXTURE = """\
State: TX
Program: Medicaid managed care
Reporting Year: 2024

== PERFORMANCE MEASURES ==
Plan: Superior HealthPlan
Measure: Controlling High Blood Pressure | Code: CBP | Steward: HEDIS | Population: Adults | Method: Hybrid | Rate: 61.7% | Numerator: 617 | Denominator: 1000

== PERFORMANCE IMPROVEMENT PROJECTS ==
Plan: Superior HealthPlan | Title: Improving Postpartum Care | Focus: Maternal Health | Baseline: 42.0% | Goal: 55.0% | Most Recent: 49.5% | Validation: Partially Met

== COMPLIANCE REVIEW ==
Plan: Superior HealthPlan | Standard: Grievances and Appeals | Determination: Compliant
"""


def test_parses_each_record_type():
    recs = EQRConnector().parse_rule_based(FIXTURE, _doc())
    assert sum(isinstance(r, EQRQualityMeasure) for r in recs) == 1
    assert sum(isinstance(r, EQRPerformanceImprovementProject) for r in recs) == 1
    assert sum(isinstance(r, EQRComplianceFinding) for r in recs) == 1


def test_measure_fields_extracted():
    recs = EQRConnector().parse_rule_based(FIXTURE, _doc())
    m = next(r for r in recs if isinstance(r, EQRQualityMeasure))
    assert m.plan_name == "Superior HealthPlan"
    assert m.measure_code == "CBP"
    assert m.measure_steward == MeasureSteward.HEDIS
    assert m.rate == 61.7
    assert m.numerator == 617 and m.denominator == 1000
    assert m.provenance.method.value == "rule"
    assert m.state == "TX"
