"""Connector for Medicaid External Quality Review (EQR) technical reports.

Federal regulation (42 CFR 438.350+) requires every state that contracts with
Medicaid/CHIP managed care organizations to have an External Quality Review
Organization (EQRO) produce an annual *technical report*. These reports are the
public-facing summary of managed-care quality oversight: validated performance
measures, performance improvement projects (PIPs), and compliance reviews.

They must be posted publicly -- but there is no central, machine-readable
repository (MACPAC recommended CMS build one in 2025). They live as ~40-300 page
PDFs scattered across state websites. This connector turns them into tidy,
provenance-stamped tables.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    EQRComplianceFinding,
    EQRPerformanceImprovementProject,
    EQRQualityMeasure,
    ExtractionMethod,
    ExtractionRecord,
    MeasureSteward,
    Provenance,
    RateUnit,
    SourceDocument,
)
from .base import CandidateDoc, Connector


def _coerce_steward(value: Optional[str]) -> MeasureSteward:
    if not value:
        return MeasureSteward.UNKNOWN
    v = value.strip().lower()
    table = {
        "hedis": MeasureSteward.HEDIS,
        "ncqa": MeasureSteward.HEDIS,
        "adult core": MeasureSteward.ADULT_CORE,
        "adult core set": MeasureSteward.ADULT_CORE,
        "child core": MeasureSteward.CHILD_CORE,
        "child core set": MeasureSteward.CHILD_CORE,
        "cahps": MeasureSteward.CAHPS,
        "state": MeasureSteward.STATE_DEFINED,
        "state-defined": MeasureSteward.STATE_DEFINED,
    }
    for key, steward in table.items():
        if key in v:
            return steward
    return MeasureSteward.OTHER


def _coerce_rate(value: Any) -> tuple[Optional[float], RateUnit]:
    """Parse a printed rate like '58.2%' or '12.3 per 1,000' into (value, unit)."""
    if value is None:
        return None, RateUnit.PERCENT
    if isinstance(value, (int, float)):
        return float(value), RateUnit.PERCENT
    s = str(value).strip().lower().replace(",", "")
    unit = RateUnit.PERCENT
    if "per 100000" in s or "per 100,000" in s:
        unit = RateUnit.PER_100000
    elif "per 1000" in s or "per 1,000" in s:
        unit = RateUnit.PER_1000
    elif "%" in s or "percent" in s:
        unit = RateUnit.PERCENT
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return (float(m.group()) if m else None), unit


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(str(value).replace(",", ""))))
    except (ValueError, TypeError):
        return None


class EQRConnector(Connector):
    dataset_id = "eqr"
    name = "Medicaid External Quality Review Technical Reports"
    description = (
        "Annual EQRO technical reports for Medicaid/CHIP managed care: validated "
        "performance measures, performance improvement projects, and compliance reviews."
    )
    version = "0.1.0"
    record_models = [EQRQualityMeasure, EQRPerformanceImprovementProject, EQRComplianceFinding]
    ensemble_fields = ["rate", "numerator", "denominator", "measure_steward"]
    identity_columns = ["state", "plan_name", "measure_name", "population", "reporting_year"]

    def ensemble_key(self, record):
        if isinstance(record, EQRQualityMeasure):
            return ("eqr_m", record.state, record.plan_name, record.measure_name,
                    record.population, record.reporting_year)
        return None

    # ----- domain axioms (verification layer) -----
    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        M = EQRQualityMeasure

        def num_le_den(r):
            if not isinstance(r, M) or r.numerator is None or r.denominator is None:
                return None, ""
            return (r.numerator <= r.denominator,
                    f"numerator {r.numerator} exceeds denominator {r.denominator}")

        def den_positive(r):
            if not isinstance(r, M) or r.denominator is None:
                return None, ""
            return (r.denominator > 0, "denominator is zero")

        def percent_range(r):
            if not isinstance(r, M) or r.rate is None or r.rate_unit != RateUnit.PERCENT:
                return None, ""
            return (0.0 <= r.rate <= 100.0, f"percent rate outside [0,100]: {r.rate}")

        def rate_matches_ratio(r):
            if (not isinstance(r, M) or r.rate is None or r.numerator is None
                    or not r.denominator or r.rate_unit != RateUnit.PERCENT):
                return None, ""
            implied = 100.0 * r.numerator / r.denominator
            return (abs(implied - r.rate) <= 1.0,
                    f"printed rate {r.rate} disagrees with implied {implied:.1f}")

        def grain_key(r):
            if isinstance(r, M):
                return (r.state, r.plan_name, r.measure_name, r.population, r.reporting_year)
            return id(r)

        def duplicate_grain(group):
            if len(group) <= 1:
                return []
            return [(r, False, f"duplicate analytic grain (x{len(group)})") for r in group]

        return [
            RecordConstraint("num_le_den", num_le_den, "hard"),
            RecordConstraint("denominator_positive", den_positive, "hard"),
            RecordConstraint("percent_in_range", percent_range, "hard"),
            RecordConstraint("rate_matches_ratio", rate_matches_ratio, "soft"),
            GroupConstraint("duplicate_grain", grain_key, duplicate_grain, "soft"),
        ]

    # ----- discovery -----
    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        docs: list[CandidateDoc] = []
        # mode A: explicit report list (offline fixtures or known URLs)
        for rep in source_entry.get("reports", []):
            docs.append(
                CandidateDoc(
                    dataset_id=self.dataset_id,
                    url=rep.get("url"),
                    local_path=rep.get("local_path"),
                    title=rep.get("title"),
                    publisher=rep.get("eqro") or source_entry.get("eqro"),
                    jurisdiction=rep.get("state") or source_entry.get("state"),
                    program=rep.get("program") or source_entry.get("program"),
                    report_year=rep.get("year"),
                )
            )
        # mode B: crawl a landing page for report PDFs (live; network)
        if source_entry.get("landing_url"):
            docs.extend(self._discover_from_landing(source_entry))
        return docs

    # default link keywords that signal an EQR technical report
    LANDING_KEYWORDS = ["external quality", "eqr", "technical report", "quality review"]

    def _discover_from_landing(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        from .. import crawl

        landing = source_entry["landing_url"]
        try:
            if source_entry.get("render"):  # JS-rendered listing -> headless browser
                html = crawl.fetch_rendered_html(landing)
            else:
                from ..fetch import _download

                html = _download(landing).decode("utf-8", "ignore")
        except Exception:
            # discovery must never crash a run; a dead/blocked landing page is skipped
            return []
        keywords = source_entry.get("landing_keywords", self.LANDING_KEYWORDS)
        links = crawl.find_report_links(
            html, landing, keywords=keywords, max_results=source_entry.get("landing_max", 3)
        )
        return [
            CandidateDoc(
                dataset_id=self.dataset_id,
                url=str(link["url"]),
                title=link.get("title"),
                publisher=source_entry.get("eqro"),
                jurisdiction=source_entry.get("state"),
                program=source_entry.get("program"),
                report_year=link.get("year"),
            )
            for link in links
        ]

    # ----- LLM extraction contract -----
    def extraction_schema(self) -> dict[str, Any]:
        measure = {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string"},
                "measure_name": {"type": "string"},
                "measure_steward": {
                    "type": "string",
                    "description": "HEDIS, Medicaid Adult Core Set, Medicaid Child Core Set, CAHPS, State-defined, or Unknown",
                },
                "measure_code": {"type": ["string", "null"]},
                "population": {"type": ["string", "null"]},
                "data_collection_method": {
                    "type": ["string", "null"],
                    "description": "administrative, hybrid, or survey",
                },
                "reporting_year": {"type": ["integer", "null"]},
                "rate": {"type": ["number", "null"], "description": "numeric rate value as printed"},
                "rate_unit": {"type": ["string", "null"], "description": "percent, per_1000, per_100000, ratio, count"},
                "numerator": {"type": ["integer", "null"]},
                "denominator": {"type": ["integer", "null"]},
                "page": {"type": ["integer", "null"], "description": "1-indexed PDF page the value appears on"},
                "confidence": {"type": ["number", "null"], "description": "0-1 confidence in this row"},
            },
            "required": ["plan_name", "measure_name"],
        }
        pip = {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string"},
                "pip_title": {"type": "string"},
                "clinical_focus_area": {"type": ["string", "null"]},
                "reporting_year": {"type": ["integer", "null"]},
                "baseline_rate": {"type": ["number", "null"]},
                "goal_rate": {"type": ["number", "null"]},
                "most_recent_rate": {"type": ["number", "null"]},
                "validation_status": {"type": ["string", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["plan_name", "pip_title"],
        }
        compliance = {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string"},
                "standard_area": {"type": "string"},
                "determination": {"type": ["string", "null"]},
                "reporting_year": {"type": ["integer", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["plan_name", "standard_area"],
        }
        return {
            "type": "object",
            "properties": {
                "state": {"type": ["string", "null"], "description": "two-letter state/territory code"},
                "program": {"type": ["string", "null"]},
                "quality_measures": {"type": "array", "items": measure},
                "performance_improvement_projects": {"type": "array", "items": pip},
                "compliance_findings": {"type": "array", "items": compliance},
            },
            "required": ["quality_measures"],
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting structured data from a Medicaid/CHIP External Quality "
            "Review (EQR) technical report produced by an External Quality Review "
            "Organization (EQRO). Extract ONLY values explicitly printed in the text; "
            "never infer, impute, or compute a rate that is not stated. If a field is "
            "absent, return null.\n\n"
            "Capture three kinds of records:\n"
            "1. quality_measures: every health plan performance measure with its rate, "
            "numerator, denominator, population, data collection method (administrative/"
            "hybrid/survey), and measure steward (HEDIS, Medicaid Adult/Child Core Set, "
            "CAHPS, or State-defined). One row per (plan, measure, population). IMPORTANT: "
            "performance-measure tables are often large and may span many pages or "
            "appendices (HEDIS/CAHPS/Core Set rate tables). Extract EVERY row you see in "
            "this chunk -- do not summarize, sample, or skip rows, and do not stop early. "
            "A CAHPS satisfaction item, a HEDIS rate, and a Core Set rate are all "
            "quality_measures (NOT compliance findings).\n"
            "2. performance_improvement_projects (PIPs): title, clinical focus, baseline/"
            "goal/most-recent rates, and validation status.\n"
            "3. compliance_findings: each standard/category reviewed and the plan's "
            "determination. The 'determination' must be a SHORT category such as "
            "Compliant, Partially Compliant, Non-Compliant, Met, Partially Met, or Not Met "
            "-- do NOT put a numeric score or percentage in determination (a numeric score "
            "belongs to a quality_measure instead).\n\n"
            "Plan names must be copied verbatim. Record the 1-indexed PDF page for each "
            "row and a 0-1 confidence reflecting how unambiguous the source text was. "
            "Rates are usually percentages; preserve the printed unit."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        # prefer the authoritative 2-letter code from the source registry over the
        # model's free-text guess, so every row for a state shares one clean code
        state = (doc.jurisdiction or payload.get("state") or "").strip().upper()
        program = payload.get("program") or doc.program
        records: list[ExtractionRecord] = []

        def prov(item: dict[str, Any]) -> Provenance:
            return Provenance(
                **provenance_base,
                page_start=item.get("page"),
                page_end=item.get("page"),
                confidence=item.get("confidence"),
            )

        for m in payload.get("quality_measures", []) or []:
            if not isinstance(m, dict):
                continue  # skip malformed items (a stray string crashed CO before)
            rate, unit = _coerce_rate(m.get("rate"))
            if m.get("rate_unit"):
                try:
                    unit = RateUnit(str(m["rate_unit"]).lower())
                except ValueError:
                    pass
            records.append(
                EQRQualityMeasure(
                    provenance=prov(m),
                    state=state or "ZZ",
                    program=program,
                    plan_name=m.get("plan_name", "").strip(),
                    measure_name=m.get("measure_name", "").strip(),
                    measure_steward=_coerce_steward(m.get("measure_steward")),
                    measure_code=m.get("measure_code"),
                    reporting_year=m.get("reporting_year") or doc.report_year or 0,
                    rate=rate,
                    rate_unit=unit,
                    numerator=_to_int(m.get("numerator")),
                    denominator=_to_int(m.get("denominator")),
                    population=m.get("population"),
                    data_collection_method=m.get("data_collection_method"),
                )
            )

        for p in payload.get("performance_improvement_projects", []) or []:
            if not isinstance(p, dict):
                continue
            records.append(
                EQRPerformanceImprovementProject(
                    provenance=prov(p),
                    state=state or "ZZ",
                    program=program,
                    plan_name=p.get("plan_name", "").strip(),
                    pip_title=p.get("pip_title", "").strip(),
                    clinical_focus_area=p.get("clinical_focus_area"),
                    reporting_year=p.get("reporting_year") or doc.report_year,
                    baseline_rate=_coerce_rate(p.get("baseline_rate"))[0],
                    goal_rate=_coerce_rate(p.get("goal_rate"))[0],
                    most_recent_rate=_coerce_rate(p.get("most_recent_rate"))[0],
                    validation_status=p.get("validation_status"),
                )
            )

        for c in payload.get("compliance_findings", []) or []:
            if not isinstance(c, dict):
                continue
            records.append(
                EQRComplianceFinding(
                    provenance=prov(c),
                    state=state or "ZZ",
                    program=program,
                    plan_name=c.get("plan_name", "").strip(),
                    standard_area=c.get("standard_area", "").strip(),
                    determination=c.get("determination"),
                    reporting_year=c.get("reporting_year") or doc.report_year,
                )
            )
        return records

    # ----- deterministic reference parser (canonical text layout) -----
    _KV = re.compile(r"([A-Za-z /]+):\s*([^|]+)")

    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        state = (doc.jurisdiction or "ZZ").upper()
        program = doc.program
        year = doc.report_year or 0
        section = None
        current_plan: Optional[str] = None

        base_prov = dict(
            source_document_id=doc.document_id,
            source_url=doc.source_url,
            method=ExtractionMethod.RULE,
            extractor_version=self.version,
        )

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("state:"):
                state = line.split(":", 1)[1].strip().upper()
                continue
            if low.startswith("program:"):
                program = line.split(":", 1)[1].strip()
                continue
            if low.startswith("reporting year:"):
                year = _to_int(line.split(":", 1)[1]) or year
                continue
            if line.startswith("=="):
                if "PERFORMANCE MEASURE" in line.upper():
                    section = "measures"
                elif "IMPROVEMENT PROJECT" in line.upper():
                    section = "pips"
                elif "COMPLIANCE" in line.upper():
                    section = "compliance"
                else:
                    section = None
                continue
            if low.startswith("plan:") and "|" not in line:
                current_plan = line.split(":", 1)[1].strip()
                continue

            fields = {k.strip().lower(): v.strip() for k, v in self._KV.findall(line)}
            if not fields:
                continue
            plan = fields.get("plan", current_plan) or current_plan

            if section == "measures" and "measure" in fields and plan:
                rate, unit = _coerce_rate(fields.get("rate"))
                records.append(
                    EQRQualityMeasure(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state,
                        program=program,
                        plan_name=plan,
                        measure_name=fields["measure"],
                        measure_steward=_coerce_steward(fields.get("steward")),
                        measure_code=fields.get("code"),
                        reporting_year=year,
                        rate=rate,
                        rate_unit=unit,
                        numerator=_to_int(fields.get("numerator")),
                        denominator=_to_int(fields.get("denominator")),
                        population=fields.get("population"),
                        data_collection_method=fields.get("method"),
                    )
                )
            elif section == "pips" and "title" in fields and plan:
                records.append(
                    EQRPerformanceImprovementProject(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state,
                        program=program,
                        plan_name=plan,
                        pip_title=fields["title"],
                        clinical_focus_area=fields.get("focus"),
                        reporting_year=year,
                        baseline_rate=_coerce_rate(fields.get("baseline"))[0],
                        goal_rate=_coerce_rate(fields.get("goal"))[0],
                        most_recent_rate=_coerce_rate(fields.get("most recent"))[0],
                        validation_status=fields.get("validation"),
                    )
                )
            elif section == "compliance" and "standard" in fields and plan:
                records.append(
                    EQRComplianceFinding(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state,
                        program=program,
                        plan_name=plan,
                        standard_area=fields["standard"],
                        determination=fields.get("determination"),
                        reporting_year=year,
                    )
                )
        return records
