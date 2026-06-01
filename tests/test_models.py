from dark_health_data.models import (
    EQRQualityMeasure,
    ExtractionMethod,
    Provenance,
    QAStatus,
    RateUnit,
)


def _prov():
    return Provenance(source_document_id="abc", method=ExtractionMethod.RULE)


def _measure(**kw):
    base = dict(
        provenance=_prov(),
        state="tx",
        plan_name="Test Plan",
        measure_name="Controlling High Blood Pressure",
        reporting_year=2024,
    )
    base.update(kw)
    return EQRQualityMeasure(**base)


def test_state_is_uppercased():
    assert _measure().state == "TX"


def test_valid_measure_passes():
    m = _measure(rate=61.7, numerator=617, denominator=1000)
    assert m.qa_status == QAStatus.PASS
    assert m.qa_flags == []


def test_numerator_gt_denominator_fails():
    m = _measure(rate=49.3, numerator=2100, denominator=2000)
    assert m.qa_status == QAStatus.FAIL
    assert any("numerator" in f for f in m.qa_flags)


def test_zero_denominator_fails():
    m = _measure(numerator=5, denominator=0)
    assert m.qa_status == QAStatus.FAIL


def test_percent_out_of_range_fails():
    m = _measure(rate=150.0, rate_unit=RateUnit.PERCENT)
    assert m.qa_status == QAStatus.FAIL


def test_non_percent_rate_not_range_checked():
    m = _measure(rate=120.0, rate_unit=RateUnit.PER_1000)
    assert m.qa_status == QAStatus.PASS
