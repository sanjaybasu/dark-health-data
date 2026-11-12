"""Core data models for Hidden Health Data.

Every value we extract from a source document is *evidence*, not ground truth.
To be usable for science, each record must be traceable back to the exact
document, page, and method that produced it, and must carry an explicit
quality status. These models encode that contract.

The design is deliberately source-agnostic at the base layer (`Provenance`,
`SourceDocument`, `ExtractionRecord`) with dataset-specific record types layered
on top (e.g. the Medicaid EQR records). New datasets add new record types; they
do not change the provenance/quality contract.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Provenance + quality contract (shared by every dataset)
# ---------------------------------------------------------------------------


class ExtractionMethod(str, Enum):
    """How a value was obtained from the source document."""

    LLM = "llm"  # large language model structured extraction
    VISION = "vision"  # vision-language read of a rendered page image (charts/figures)
    RULE = "rule"  # deterministic parser (regex/table heuristics)
    MANUAL = "manual"  # human keyed/corrected
    DERIVED = "derived"  # computed from other extracted fields


class QAStatus(str, Enum):
    """Result of automated validation for a single record."""

    PASS = "pass"  # passed all checks
    WARN = "warn"  # plausible but with caveats (see qa_flags)
    FAIL = "fail"  # failed a hard logical/schema check; do not use blindly


class Provenance(BaseModel):
    """Where a record came from and how confident we are in it."""

    source_document_id: str = Field(..., description="sha256 of the source document bytes")
    source_url: Optional[str] = Field(None, description="canonical public URL of the document")
    page_start: Optional[int] = Field(None, description="1-indexed first page the value was found on")
    page_end: Optional[int] = Field(None, description="1-indexed last page (inclusive)")
    method: ExtractionMethod = Field(..., description="extraction method")
    model_name: Optional[str] = Field(None, description="model id if method=llm, e.g. claude-sonnet-4-6")
    extractor_version: Optional[str] = Field(None, description="version of the connector/extractor code")
    confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="model/parser self-reported confidence in [0,1]"
    )
    extracted_at: datetime = Field(default_factory=utcnow)
    reviewer: Optional[str] = Field(None, description="human reviewer, if manually verified")
    notes: Optional[str] = Field(None, description="free-text provenance notes / snippet")
    source_span: Optional[str] = Field(
        None, description="verbatim source text the value was read from (enables grounding checks)"
    )
    bbox: Optional[list[float]] = Field(
        None, description="[x0,y0,x1,y1] bounding box on the page, if the parser provides one"
    )


class SourceDocument(BaseModel):
    """A single source artifact (typically a PDF) and its retrieval metadata."""

    document_id: str = Field(..., description="sha256 of the raw document bytes (stable id)")
    source_url: Optional[str] = None
    local_path: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = Field(None, description="state agency / EQRO / hospital that issued it")
    dataset_id: str = Field(..., description="which dataset family this doc belongs to, e.g. 'eqr'")
    jurisdiction: Optional[str] = Field(None, description="state/territory two-letter code or name")
    program: Optional[str] = Field(None, description="e.g. 'Medicaid managed care', 'CHIP'")
    report_year: Optional[int] = None
    retrieved_at: Optional[datetime] = None
    content_sha256: Optional[str] = Field(None, description="sha256 of extracted text (drift detection)")
    n_pages: Optional[int] = None
    mime_type: Optional[str] = "application/pdf"
    license: Optional[str] = Field(
        "public-record", description="rights basis for redistribution of derived data"
    )

    @classmethod
    def from_bytes(cls, raw: bytes, *, dataset_id: str, **kwargs: Any) -> "SourceDocument":
        doc_id = hashlib.sha256(raw).hexdigest()
        return cls(document_id=doc_id, dataset_id=dataset_id, **kwargs)


class ExtractionRecord(BaseModel):
    """Base class for every dataset-specific record.

    Subclasses add domain fields. The base guarantees provenance + QA so that
    downstream curation and publication can treat all records uniformly.
    """

    record_type: str = Field(..., description="discriminator, e.g. 'eqr_quality_measure'")
    provenance: Provenance
    qa_status: QAStatus = QAStatus.PASS
    qa_flags: list[str] = Field(default_factory=list)
    trust_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="fused verification trust score in [0,1]"
    )
    review_recommended: bool = Field(
        False, description="flagged for human review by the verification gate"
    )

    def flag(self, message: str, *, fail: bool = False) -> None:
        """Attach a QA flag (deduplicated), escalating qa_status as appropriate."""
        if message not in self.qa_flags:
            self.qa_flags.append(message)
        if fail:
            self.qa_status = QAStatus.FAIL
        elif self.qa_status == QAStatus.PASS:
            self.qa_status = QAStatus.WARN


# ---------------------------------------------------------------------------
# Medicaid External Quality Review (EQR) dataset records
# ---------------------------------------------------------------------------


class MeasureSteward(str, Enum):
    HEDIS = "HEDIS"
    ADULT_CORE = "Medicaid Adult Core Set"
    CHILD_CORE = "Medicaid Child Core Set"
    CAHPS = "CAHPS"
    STATE_DEFINED = "State-defined"
    OTHER = "Other"
    UNKNOWN = "Unknown"


class RateUnit(str, Enum):
    PERCENT = "percent"
    PER_1000 = "per_1000"
    PER_100000 = "per_100000"
    RATIO = "ratio"
    COUNT = "count"
    OTHER = "other"


class EQRQualityMeasure(ExtractionRecord):
    """One performance-measure observation for one plan, in one EQR report.

    Grain: (state, plan, measure, reporting_year, population). This is the
    primary "long" table researchers will analyze.
    """

    record_type: str = "eqr_quality_measure"

    state: str = Field(..., description="two-letter state/territory code, uppercased")
    program: Optional[str] = Field(None, description="e.g. 'Medicaid managed care', 'CHIP'")
    plan_name: str = Field(..., description="managed care organization / plan name as printed")
    plan_id: Optional[str] = Field(None, description="state or federal plan identifier if printed")
    measure_name: str = Field(..., description="performance measure name as printed")
    measure_steward: MeasureSteward = MeasureSteward.UNKNOWN
    measure_code: Optional[str] = Field(None, description="abbreviation/code if printed, e.g. 'W30', 'CBP'")
    reporting_year: int = Field(
        ..., description="measurement year the rate covers (the table/column-header year, "
        "e.g. HEDIS MY or CMS FFY) — NOT the report's publication year")
    rate: Optional[float] = Field(None, description="reported rate value")
    rate_unit: RateUnit = RateUnit.PERCENT
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    population: Optional[str] = Field(None, description="e.g. 'Adults', 'Children', 'Total'")
    data_collection_method: Optional[str] = Field(None, description="administrative / hybrid / survey")

    @field_validator("state")
    @classmethod
    def _upper_state(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _logical_checks(self) -> "EQRQualityMeasure":
        # These run at construction time; validate.py adds dataset-level checks.
        if self.numerator is not None and self.denominator is not None:
            if self.denominator == 0:
                self.flag("denominator is zero", fail=True)
            elif self.numerator > self.denominator:
                self.flag(
                    f"numerator ({self.numerator}) > denominator ({self.denominator})",
                    fail=True,
                )
        if self.rate is not None and self.rate_unit == RateUnit.PERCENT:
            if self.rate < 0 or self.rate > 100:
                self.flag(f"percent rate out of [0,100]: {self.rate}", fail=True)
        return self


class EQRPerformanceImprovementProject(ExtractionRecord):
    """A Performance Improvement Project (PIP) validated during the EQR."""

    record_type: str = "eqr_pip"

    state: str
    program: Optional[str] = None
    plan_name: str
    pip_title: str
    clinical_focus_area: Optional[str] = None
    reporting_year: Optional[int] = None
    baseline_rate: Optional[float] = None
    goal_rate: Optional[float] = None
    most_recent_rate: Optional[float] = None
    validation_status: Optional[str] = Field(None, description="e.g. 'Met', 'Partially Met', 'Not Met'")

    @field_validator("state")
    @classmethod
    def _upper_state(cls, v: str) -> str:
        return v.strip().upper()


class EQRComplianceFinding(ExtractionRecord):
    """A compliance/standards review determination for a plan."""

    record_type: str = "eqr_compliance"

    state: str
    program: Optional[str] = None
    plan_name: str
    standard_area: str = Field(..., description="e.g. 'Grievances and Appeals', 'Availability of Services'")
    determination: Optional[str] = Field(None, description="e.g. 'Compliant', 'Partially Compliant', 'Not Compliant'")
    reporting_year: Optional[int] = None

    @field_validator("state")
    @classmethod
    def _upper_state(cls, v: str) -> str:
        return v.strip().upper()


# ---------------------------------------------------------------------------
# Hospital Community Health Needs Assessment (CHNA) dataset records
# ---------------------------------------------------------------------------


class CHNAIdentifiedNeed(ExtractionRecord):
    """A community health need identified in a hospital's CHNA."""

    record_type: str = "chna_identified_need"

    hospital_name: str = Field(..., description="reporting hospital/system name as printed")
    hospital_ein: Optional[str] = Field(None, description="IRS EIN if printed")
    state: Optional[str] = None
    report_year: Optional[int] = None
    need: str = Field(..., description="identified community health need as printed")
    domain: Optional[str] = Field(
        None, description="normalized domain, e.g. 'Mental health', 'Access to care', 'Housing'"
    )
    is_priority: Optional[bool] = Field(None, description="flagged as a prioritized need")
    priority_rank: Optional[int] = Field(None, description="rank if the CHNA orders priorities")


class CHNAImplementationStrategy(ExtractionRecord):
    """A planned strategy/commitment a hospital made to address a need."""

    record_type: str = "chna_strategy"

    hospital_name: str
    state: Optional[str] = None
    report_year: Optional[int] = None
    need_addressed: Optional[str] = None
    strategy: str = Field(..., description="planned action/investment as printed")
    measurable_objective: Optional[str] = None


# ---------------------------------------------------------------------------
# Maternal Mortality Review Committee (MMRC) dataset records
# ---------------------------------------------------------------------------


class MMRCFinding(ExtractionRecord):
    """A quantitative finding from a state MMRC report (often stratified)."""

    record_type: str = "mmrc_finding"

    state: str
    report_period: Optional[str] = Field(None, description="years covered, e.g. '2019-2021'")
    population_group: Optional[str] = Field(
        None, description="stratum, e.g. 'Overall', 'Black, non-Hispanic'"
    )
    pregnancy_related_deaths: Optional[int] = None
    pregnancy_related_mortality_ratio: Optional[float] = Field(
        None, description="deaths per 100,000 live births"
    )
    pct_preventable: Optional[float] = Field(None, description="percent deemed preventable, 0-100")
    leading_cause: Optional[str] = None

    @field_validator("state")
    @classmethod
    def _strip_state(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _checks(self) -> "MMRCFinding":
        if self.pct_preventable is not None and not (0 <= self.pct_preventable <= 100):
            self.flag(f"pct_preventable out of [0,100]: {self.pct_preventable}", fail=True)
        if (
            self.pregnancy_related_mortality_ratio is not None
            and self.pregnancy_related_mortality_ratio < 0
        ):
            self.flag("negative mortality ratio", fail=True)
        return self


class MMRCRecommendation(ExtractionRecord):
    """A prevention recommendation issued by a state MMRC."""

    record_type: str = "mmrc_recommendation"

    state: str
    report_year: Optional[int] = None
    recommendation: str
    category: Optional[str] = Field(None, description="topic, e.g. 'Mental/behavioral health'")
    target_level: Optional[str] = Field(
        None, description="who acts, e.g. 'Provider', 'Facility', 'Community', 'Patient/Family'"
    )

    @field_validator("state")
    @classmethod
    def _strip_state(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Medicaid Section 1115 demonstration evaluation records
# ---------------------------------------------------------------------------


WAIVER_1115_DIRECTIONS = {
    "improved", "no significant change", "worsened", "mixed", "not estimated", "descriptive",
}


class Waiver1115Finding(ExtractionRecord):
    """One evaluation finding from a Section 1115 demonstration evaluation report.

    Grain: (state, demonstration, intervention, outcome, population, report_year).
    Built to support cross-state synthesis of what demonstrations tested (especially
    health-related social needs such as food/nutrition) and what the evaluations found.
    """

    record_type: str = "waiver_1115_finding"

    state: str
    demonstration_name: str = Field(..., description="the 1115 demonstration, e.g. 'Healthy Opportunities Pilots'")
    waiver_id: Optional[str] = Field(None, description="CMS demonstration number if printed, e.g. '11-W-00313/4'")
    report_year: Optional[int] = None
    evaluation_period: Optional[str] = Field(None, description="period the evaluation covers, e.g. '2022-2024'")
    evaluator: Optional[str] = Field(None, description="independent evaluator, if named")
    intervention: Optional[str] = Field(None, description="service tested, e.g. 'Medically tailored meals'")
    domain: Optional[str] = Field(
        None, description="normalized domain, e.g. 'Food/Nutrition', 'Housing', 'Transportation'"
    )
    outcome_measure: Optional[str] = Field(
        None, description="outcome studied, e.g. 'Food insecurity', 'ED visits', 'HbA1c'"
    )
    population: Optional[str] = None
    effect_direction: Optional[str] = Field(
        None, description="improved / no significant change / worsened / mixed / not estimated / descriptive"
    )
    value: Optional[float] = Field(None, description="reported effect estimate, if quantified")
    value_unit: Optional[str] = Field(None, description="unit of `value`, e.g. 'percentage points', 'percent'")
    significance: Optional[str] = Field(None, description="significance as printed, e.g. 'p<0.05', 'NS'")
    result: Optional[str] = Field(None, description="short finding as printed/paraphrased")

    @field_validator("state")
    @classmethod
    def _strip_state(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _checks(self) -> "Waiver1115Finding":
        if self.report_year is not None and not (1990 <= self.report_year <= 2035):
            self.flag(f"implausible report_year: {self.report_year}", fail=True)
        if self.effect_direction and self.effect_direction.strip().lower() not in WAIVER_1115_DIRECTIONS:
            self.flag(f"unrecognized effect_direction: {self.effect_direction}")
        return self


class Waiver1115Recommendation(ExtractionRecord):
    """A recommendation or lesson from a Section 1115 demonstration evaluation."""

    record_type: str = "waiver_1115_recommendation"

    state: str
    demonstration_name: Optional[str] = None
    report_year: Optional[int] = None
    recommendation: str
    category: Optional[str] = Field(None, description="topic, e.g. 'Operations', 'Measurement', 'Policy/financing'")

    @field_validator("state")
    @classmethod
    def _strip_state(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Nursing-home Statement of Deficiencies (CMS Form 2567) records
# ---------------------------------------------------------------------------


# CMS scope/severity grid letters (scope: isolated/pattern/widespread ×
# severity: minimal/potential-harm/actual-harm/immediate-jeopardy).
NURSING_HOME_SCOPE_SEVERITY = set("ABCDEFGHIJKL")


class NursingHomeDeficiency(ExtractionRecord):
    """One deficiency cited on a nursing-home Statement of Deficiencies (CMS-2567).

    Grain: (state, ccn/facility, ftag, report_year). Captures the surveyor's
    narrative finding -- the "why" behind a deficiency that Care Compare's tag/severity
    counts omit.
    """

    record_type: str = "nursing_home_deficiency"

    facility_name: str
    ccn: Optional[str] = Field(None, description="CMS Certification Number (6-char provider id) if printed")
    state: str
    survey_date: Optional[str] = Field(None, description="survey/inspection date as printed")
    report_year: Optional[int] = None
    ftag: Optional[str] = Field(None, description="federal deficiency tag, e.g. 'F689'")
    ftag_description: Optional[str] = Field(None, description="the tag's regulatory title as printed")
    scope_severity: Optional[str] = Field(None, description="single scope/severity letter A-L")
    deficiency_description: Optional[str] = Field(None, description="surveyor's narrative finding")

    @field_validator("state")
    @classmethod
    def _upper_state(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _checks(self) -> "NursingHomeDeficiency":
        if self.report_year is not None and not (1990 <= self.report_year <= 2035):
            self.flag(f"implausible report_year: {self.report_year}", fail=True)
        if self.scope_severity:
            self.scope_severity = self.scope_severity.strip().upper()
            if self.scope_severity not in NURSING_HOME_SCOPE_SEVERITY:
                self.flag(f"scope_severity not a valid A-L letter: {self.scope_severity}")
        return self


class NursingHomePlanOfCorrection(ExtractionRecord):
    """A facility's plan of correction for a cited deficiency (CMS-2567)."""

    record_type: str = "nursing_home_plan_of_correction"

    facility_name: str
    state: str
    report_year: Optional[int] = None
    ftag: Optional[str] = Field(None, description="the deficiency tag this corrects, e.g. 'F689'")
    correction: str
    completion_date: Optional[str] = None

    @field_validator("state")
    @classmethod
    def _upper_state(cls, v: str) -> str:
        return v.strip().upper()


# Registry of record types -> the table they curate into.
# ---------------------------------------------------------------------------
# Medicaid fair-hearing decisions
# ---------------------------------------------------------------------------

#: allowed values of FairHearingDecision.outcome (the appellant's result)
FAIR_HEARING_OUTCOMES = {
    "granted", "denied", "granted in part", "remanded", "dismissed", "withdrawn", "other",
}


class FairHearingDecision(ExtractionRecord):
    """One state Medicaid fair-hearing (administrative appeal) decision.

    Grain: (state, decision_id). Case-level fields only. The record never carries the
    appellant's identity, age, diagnosis, or the narrative findings of fact, even when
    the state's own redaction left them in the source text.
    """

    record_type: str = "fair_hearing_decision"

    state: str
    decision_id: str = Field(..., description="state's published decision identifier")
    decision_category: Optional[str] = Field(
        None, description="state's own index category, e.g. 'Medical Services'")
    decision_year: Optional[int] = None
    program: Optional[str] = Field(
        None, description="program or coverage group, e.g. 'HUSKY D', 'Long-term care Medicaid'")
    respondent: Optional[str] = Field(
        None, description="entity whose action was appealed: state agency, MCO, ASO, facility")
    respondent_type: Optional[str] = Field(
        None, description="state agency / managed care organization / administrative services "
        "organization / nursing facility / marketplace / other")
    action_type: Optional[str] = Field(
        None, description="denial / termination / reduction / suspension / prior authorization "
        "denial / discharge / overpayment / calculation / penalty / effective date / other")
    issue_category: Optional[str] = Field(
        None, description="eligibility - income / eligibility - assets or transfer / "
        "eligibility - procedural or verification / eligibility - other / covered service - "
        "medical necessity / covered service - other / nursing facility discharge / other")
    service_at_issue: Optional[str] = Field(None, description="benefit or service, if any")
    issue_statement: Optional[str] = Field(None, description="the decision's statement of the issue")
    outcome: Optional[str] = Field(None, description="one of FAIR_HEARING_OUTCOMES")
    outcome_reason: Optional[str] = Field(None, description="short reason for the outcome")
    remedy_ordered: Optional[str] = Field(None, description="what the order directs, if anything")
    amount_at_issue: Optional[float] = Field(None, description="dollar amount in dispute, if printed")
    legal_authorities: Optional[str] = Field(
        None, description="statutes, regulations, and policy sections cited, '; '-separated")
    hearing_officer: Optional[str] = Field(None, description="name of the presiding officer")

    @field_validator("state")
    @classmethod
    def _strip_state(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _checks(self) -> "FairHearingDecision":
        if self.decision_year is not None and not (1990 <= self.decision_year <= 2035):
            self.flag(f"implausible decision_year: {self.decision_year}", fail=True)
        if self.outcome and self.outcome.strip().lower() not in FAIR_HEARING_OUTCOMES:
            self.flag(f"unrecognized outcome: {self.outcome}")
        if self.amount_at_issue is not None and self.amount_at_issue < 0:
            self.flag("negative amount_at_issue")
        return self


# ---------------------------------------------------------------------------
# Medicaid managed care (MCO) contract provisions
# ---------------------------------------------------------------------------

#: allowed values of MCOContractProvision.provision_category
MCO_PROVISION_CATEGORIES = (
    "care coordination and care management",
    "health risk assessment and screening",
    "community health workers",
    "health-related social needs and in lieu of services",
    "behavioral health integration",
    "value-based payment",
    "quality incentives and withholds",
    "network adequacy and access",
    "prior authorization and utilization management",
    "member outreach and engagement",
)


class MCOContractProvision(ExtractionRecord):
    """One requirement in a state Medicaid managed care contract (or amendment).

    Grain: (state, document_title, page, section, provision_category, requirement). The
    verbatim contract text the row rests on is in ``provenance.source_span``.
    """

    record_type: str = "mco_contract_provision"

    state: str
    document_title: Optional[str] = Field(None, description="contract or amendment title")
    document_type: Optional[str] = Field(
        None, description="model contract / signed contract / amendment / exhibit / other")
    contract_year: Optional[int] = Field(None, description="year the document takes effect")
    section: Optional[str] = Field(None, description="section number and heading, as printed")
    provision_category: Optional[str] = Field(None, description="one of MCO_PROVISION_CATEGORIES")
    requirement: Optional[str] = Field(None, description="one-sentence statement of the requirement")
    target_population: Optional[str] = Field(
        None, description="members the requirement applies to, if limited (e.g. 'high-risk members')")
    numeric_value: Optional[float] = Field(
        None, description="the requirement's number, if any (e.g. 90 for 'within 90 days')")
    numeric_unit: Optional[str] = Field(
        None, description="unit of numeric_value: days / business days / hours / minutes / "
        "months / years / percent / dollars / miles / members per staff / count / other")
    obligation: Optional[str] = Field(
        None, description="must / may / incentive / penalty")

    @field_validator("state")
    @classmethod
    def _strip_state(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _checks(self) -> "MCOContractProvision":
        if self.contract_year is not None and not (1990 <= self.contract_year <= 2035):
            self.flag(f"implausible contract_year: {self.contract_year}", fail=True)
        if self.provision_category and self.provision_category not in MCO_PROVISION_CATEGORIES:
            self.flag(f"provision_category outside the controlled list: {self.provision_category}")
        if self.numeric_value is not None and self.numeric_unit == "percent" and not (
                0 <= self.numeric_value <= 100):
            self.flag(f"percent outside 0-100: {self.numeric_value}")
        if self.numeric_value is not None and not self.numeric_unit:
            self.flag("numeric_value without a unit")
        return self


RECORD_TABLE = {
    "eqr_quality_measure": "eqr_quality_measures",
    "eqr_pip": "eqr_performance_improvement_projects",
    "eqr_compliance": "eqr_compliance_findings",
    "chna_identified_need": "chna_identified_needs",
    "chna_strategy": "chna_implementation_strategies",
    "mmrc_finding": "mmrc_findings",
    "mmrc_recommendation": "mmrc_recommendations",
    "waiver_1115_finding": "waiver_1115_findings",
    "waiver_1115_recommendation": "waiver_1115_recommendations",
    "nursing_home_deficiency": "nursing_home_deficiencies",
    "nursing_home_plan_of_correction": "nursing_home_plans_of_correction",
    "fair_hearing_decision": "fair_hearing_decisions",
    "mco_contract_provision": "mco_contract_provisions",
}
