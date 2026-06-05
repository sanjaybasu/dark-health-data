"""Connector for Medicaid Section 1115 demonstration evaluation reports.

CMS requires every Section 1115 demonstration to be independently evaluated, and
states publish interim and summative evaluation reports as long PDFs on Medicaid.gov
and state sites. These describe the services tested (increasingly health-related
social needs such as food/nutrition, housing, and transportation), the outcomes
studied, and what the evaluation found -- but as narrative text with no common
schema, so cross-state synthesis (e.g. of food and nutrition interventions) is
impractical. This connector extracts the evaluation findings and recommendations.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    ExtractionMethod,
    ExtractionRecord,
    Provenance,
    SourceDocument,
    Waiver1115Finding,
    Waiver1115Recommendation,
    WAIVER_1115_DIRECTIONS,
)
from .base import CandidateDoc, Connector


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(round(float(str(value).replace(",", ""))))
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


class Waiver1115Connector(Connector):
    dataset_id = "waiver_1115"
    name = "Medicaid Section 1115 Demonstration Evaluations"
    description = (
        "Evaluation findings (intervention, outcome, population, effect direction and "
        "size) and recommendations extracted from Section 1115 demonstration evaluation "
        "reports, with a focus on health-related social needs (food/nutrition, housing)."
    )
    version = "0.1.0"
    record_models = [Waiver1115Finding, Waiver1115Recommendation]
    ensemble_fields = ["effect_direction", "value", "outcome_measure"]
    identity_columns = [
        "state", "demonstration_name", "intervention", "outcome_measure", "population", "report_year"
    ]

    def ensemble_key(self, record):
        if isinstance(record, Waiver1115Finding):
            return ("w1115_f", record.state, record.demonstration_name, record.intervention,
                    record.outcome_measure, record.population, record.report_year)
        return None

    # ----- domain axioms (verification layer) -----
    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        F = Waiver1115Finding

        def known_direction(r):
            if not isinstance(r, F) or not r.effect_direction:
                return None, ""
            return (r.effect_direction.strip().lower() in WAIVER_1115_DIRECTIONS,
                    f"unrecognized effect_direction: {r.effect_direction}")

        def grain(r):
            if isinstance(r, F):
                return (r.state, r.demonstration_name, r.intervention, r.outcome_measure,
                        r.population, r.report_year)
            return id(r)

        def duplicate_grain(group):
            if len(group) <= 1:
                return []
            return [(r, False, f"duplicate finding grain (x{len(group)})") for r in group]

        return [
            RecordConstraint("known_effect_direction", known_direction, "soft"),
            GroupConstraint("duplicate_grain", grain, duplicate_grain, "soft"),
        ]

    # ----- discovery -----
    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        docs: list[CandidateDoc] = []
        for rep in source_entry.get("reports", []):
            docs.append(
                CandidateDoc(
                    dataset_id=self.dataset_id,
                    url=rep.get("url"),
                    local_path=rep.get("local_path"),
                    title=rep.get("title"),
                    publisher=rep.get("evaluator") or source_entry.get("evaluator"),
                    jurisdiction=rep.get("state") or source_entry.get("state"),
                    program=rep.get("demonstration") or source_entry.get("demonstration"),
                    report_year=rep.get("year"),
                )
            )
        return docs

    # ----- LLM extraction contract -----
    def extraction_schema(self) -> dict[str, Any]:
        finding = {
            "type": "object",
            "properties": {
                "intervention": {"type": ["string", "null"], "description": "service tested, e.g. 'Medically tailored meals'"},
                "domain": {"type": ["string", "null"], "description": "Food/Nutrition, Housing, Transportation, Care management, Behavioral health, Employment, Other"},
                "outcome_measure": {"type": ["string", "null"], "description": "e.g. 'Food insecurity', 'ED visits', 'HbA1c'"},
                "population": {"type": ["string", "null"]},
                "effect_direction": {"type": ["string", "null"], "description": "improved / no significant change / worsened / mixed / not estimated / descriptive"},
                "value": {"type": ["number", "null"], "description": "effect estimate if quantified"},
                "value_unit": {"type": ["string", "null"], "description": "e.g. 'percentage points', 'percent'"},
                "significance": {"type": ["string", "null"], "description": "e.g. 'p<0.05', 'NS'"},
                "result": {"type": ["string", "null"], "description": "short finding as printed"},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
        }
        rec = {
            "type": "object",
            "properties": {
                "recommendation": {"type": "string"},
                "category": {"type": ["string", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["recommendation"],
        }
        return {
            "type": "object",
            "properties": {
                "state": {"type": ["string", "null"]},
                "demonstration_name": {"type": ["string", "null"], "description": "the 1115 demonstration"},
                "waiver_id": {"type": ["string", "null"], "description": "CMS demonstration number if printed"},
                "report_year": {"type": ["integer", "null"]},
                "evaluation_period": {"type": ["string", "null"]},
                "evaluator": {"type": ["string", "null"]},
                "findings": {"type": "array", "items": finding},
                "recommendations": {"type": "array", "items": rec},
            },
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting structured data from an independent evaluation report "
            "for a Medicaid Section 1115 demonstration. Extract ONLY values explicitly "
            "present; never infer an effect that is not stated.\n\n"
            "Capture two kinds of records:\n"
            "1. findings -- one row per (intervention, outcome, population). For each, "
            "record the service/intervention tested, its domain (Food/Nutrition, Housing, "
            "Transportation, Care management, Behavioral health, Employment, or Other), the "
            "outcome measure, the population, the direction of effect (use exactly one of: "
            "improved, no significant change, worsened, mixed, not estimated, descriptive), "
            "the numeric effect and its unit if reported, the significance as printed "
            "(e.g. 'p<0.05', 'NS'), and a short result statement. Pay particular attention "
            "to food and nutrition interventions.\n"
            "2. recommendations -- each recommendation or lesson and a short topic category.\n\n"
            "Record the page and a 0-1 confidence for each row."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        state = payload.get("state") or doc.jurisdiction or "Unknown"
        demo = payload.get("demonstration_name") or doc.program or "Unknown demonstration"
        year = payload.get("report_year") or doc.report_year
        period = payload.get("evaluation_period")
        evaluator = payload.get("evaluator") or doc.publisher
        waiver_id = payload.get("waiver_id")
        records: list[ExtractionRecord] = []

        def prov(item: dict[str, Any]) -> Provenance:
            return Provenance(
                **provenance_base, page_start=item.get("page"), page_end=item.get("page"),
                confidence=item.get("confidence"),
            )

        for f in payload.get("findings", []) or []:
            if not isinstance(f, dict):
                continue
            records.append(
                Waiver1115Finding(
                    provenance=prov(f), state=state, demonstration_name=demo, waiver_id=waiver_id,
                    report_year=year, evaluation_period=period, evaluator=evaluator,
                    intervention=f.get("intervention"), domain=f.get("domain"),
                    outcome_measure=f.get("outcome_measure"), population=f.get("population"),
                    effect_direction=f.get("effect_direction"), value=_to_float(f.get("value")),
                    value_unit=f.get("value_unit"), significance=f.get("significance"),
                    result=f.get("result"),
                )
            )
        for r in payload.get("recommendations", []) or []:
            if not isinstance(r, dict):
                continue
            records.append(
                Waiver1115Recommendation(
                    provenance=prov(r), state=state, demonstration_name=demo, report_year=year,
                    recommendation=r.get("recommendation", "").strip(), category=r.get("category"),
                )
            )
        return records

    # ----- deterministic reference parser (canonical text layout) -----
    _KV = re.compile(r"([A-Za-z /\-]+):\s*([^|]+)")

    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        state = doc.jurisdiction or "Unknown"
        demo = doc.program or "Unknown demonstration"
        year = doc.report_year
        period: Optional[str] = None
        evaluator: Optional[str] = doc.publisher
        section = None
        base_prov = dict(
            source_document_id=doc.document_id, source_url=doc.source_url,
            method=ExtractionMethod.RULE, extractor_version=self.version,
        )
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("state:"):
                state = line.split(":", 1)[1].strip()
                continue
            if low.startswith("demonstration:"):
                demo = line.split(":", 1)[1].strip()
                continue
            if low.startswith("report year:"):
                year = _to_int(line.split(":", 1)[1]) or year
                continue
            if low.startswith("evaluation period:"):
                period = line.split(":", 1)[1].strip()
                continue
            if low.startswith("evaluator:"):
                evaluator = line.split(":", 1)[1].strip()
                continue
            if line.startswith("=="):
                if "FINDING" in line.upper():
                    section = "findings"
                elif "RECOMMENDATION" in line.upper():
                    section = "recommendations"
                else:
                    section = None
                continue
            fields = {k.strip().lower(): v.strip() for k, v in self._KV.findall(line)}
            if section == "findings" and ("outcome" in fields or "intervention" in fields):
                records.append(
                    Waiver1115Finding(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state, demonstration_name=demo, report_year=year,
                        evaluation_period=period, evaluator=evaluator,
                        intervention=fields.get("intervention"), domain=fields.get("domain"),
                        outcome_measure=fields.get("outcome"), population=fields.get("population"),
                        effect_direction=fields.get("direction"), value=_to_float(fields.get("value")),
                        value_unit=fields.get("unit"), significance=fields.get("significance"),
                        result=fields.get("result"),
                    )
                )
            elif section == "recommendations" and "recommendation" in fields:
                records.append(
                    Waiver1115Recommendation(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state, demonstration_name=demo, report_year=year,
                        recommendation=fields["recommendation"], category=fields.get("category"),
                    )
                )
        return records
