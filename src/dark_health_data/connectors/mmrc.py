"""Connector for Maternal Mortality Review Committee (MMRC) reports.

Most states convene an MMRC that reviews pregnancy-related deaths and publishes a
report with cause-of-death patterns, preventability determinations (often
stratified by race/ethnicity), and prevention recommendations. These are central
to maternal-health equity but are published as narrative state PDFs with no common
schema. This connector extracts the quantitative findings and the recommendations.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    ExtractionMethod,
    ExtractionRecord,
    MMRCFinding,
    MMRCRecommendation,
    Provenance,
    SourceDocument,
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


class MMRCConnector(Connector):
    dataset_id = "mmrc"
    name = "Maternal Mortality Review Committee Reports"
    description = (
        "Pregnancy-related death counts, mortality ratios, preventability, and "
        "prevention recommendations extracted from state MMRC reports."
    )
    version = "0.1.0"
    record_models = [MMRCFinding, MMRCRecommendation]
    ensemble_fields = ["pregnancy_related_deaths", "pregnancy_related_mortality_ratio", "pct_preventable"]
    identity_columns = ["state", "population_group", "report_period"]

    def ensemble_key(self, record):
        if isinstance(record, MMRCFinding):
            return ("mmrc_f", record.state, record.population_group, record.report_period)
        return None

    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        F = MMRCFinding

        def pct_range(r):
            if not isinstance(r, F) or r.pct_preventable is None:
                return None, ""
            return (0.0 <= r.pct_preventable <= 100.0,
                    f"pct_preventable outside [0,100]: {r.pct_preventable}")

        def ratio_nonneg(r):
            if not isinstance(r, F) or r.pregnancy_related_mortality_ratio is None:
                return None, ""
            return (r.pregnancy_related_mortality_ratio >= 0, "negative mortality ratio")

        def subgroup_le_overall(group):
            findings = [r for r in group if isinstance(r, F) and r.pregnancy_related_deaths is not None]
            overall = next(
                (r for r in findings if (r.population_group or "").strip().lower() in {"overall", "total", ""}),
                None,
            )
            if overall is None:
                return []
            cap = overall.pregnancy_related_deaths
            subs = [r for r in findings if r is not overall]
            out = [(r, False, f"subgroup deaths {r.pregnancy_related_deaths} exceed overall {cap}")
                   for r in subs if r.pregnancy_related_deaths > cap]
            if sum(r.pregnancy_related_deaths for r in subs) > cap:
                out.append((overall, False, "sum of subgroup deaths exceeds overall"))
            return out

        return [
            RecordConstraint("pct_preventable_in_range", pct_range, "hard"),
            RecordConstraint("mortality_ratio_nonnegative", ratio_nonneg, "hard"),
            GroupConstraint("subgroup_le_overall", lambda r: getattr(r, "state", id(r)),
                            subgroup_le_overall, "soft"),
        ]

    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        docs: list[CandidateDoc] = []
        for rep in source_entry.get("reports", []):
            docs.append(
                CandidateDoc(
                    dataset_id=self.dataset_id,
                    url=rep.get("url"),
                    local_path=rep.get("local_path"),
                    title=rep.get("title"),
                    publisher=source_entry.get("committee"),
                    jurisdiction=rep.get("state") or source_entry.get("state"),
                    report_year=rep.get("year"),
                )
            )
        return docs

    def extraction_schema(self) -> dict[str, Any]:
        finding = {
            "type": "object",
            "properties": {
                "population_group": {"type": ["string", "null"], "description": "stratum, e.g. race/ethnicity or 'Overall'"},
                "pregnancy_related_deaths": {"type": ["integer", "null"]},
                "pregnancy_related_mortality_ratio": {"type": ["number", "null"], "description": "per 100,000 live births"},
                "pct_preventable": {"type": ["number", "null"], "description": "0-100"},
                "leading_cause": {"type": ["string", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
        }
        rec = {
            "type": "object",
            "properties": {
                "recommendation": {"type": "string"},
                "category": {"type": ["string", "null"]},
                "target_level": {"type": ["string", "null"], "description": "Provider/Facility/Community/Patient/Policy"},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["recommendation"],
        }
        return {
            "type": "object",
            "properties": {
                "state": {"type": ["string", "null"]},
                "report_period": {"type": ["string", "null"], "description": "e.g. '2019-2021'"},
                "report_year": {"type": ["integer", "null"]},
                "findings": {"type": "array", "items": finding},
                "recommendations": {"type": "array", "items": rec},
            },
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting structured data from a state Maternal Mortality "
            "Review Committee (MMRC) report. Extract ONLY explicitly stated values; "
            "never infer rates.\n\n"
            "Capture: (1) findings -- for each stratum reported (overall and any "
            "race/ethnicity or other subgroup), the number of pregnancy-related "
            "deaths, the pregnancy-related mortality ratio (per 100,000 live "
            "births), the percent of deaths determined preventable, and the leading "
            "cause. (2) recommendations -- each prevention recommendation, a topic "
            "category, and the level expected to act (Provider/Facility/Community/"
            "Patient/Policy). Record the page and a 0-1 confidence per row."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        state = payload.get("state") or doc.jurisdiction or "Unknown"
        period = payload.get("report_period")
        year = _to_int(payload.get("report_year")) or doc.report_year
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
                MMRCFinding(
                    provenance=prov(f), state=state, report_period=period,
                    population_group=f.get("population_group"),
                    pregnancy_related_deaths=_to_int(f.get("pregnancy_related_deaths")),
                    pregnancy_related_mortality_ratio=_to_float(f.get("pregnancy_related_mortality_ratio")),
                    pct_preventable=_to_float(f.get("pct_preventable")),
                    leading_cause=f.get("leading_cause"),
                )
            )
        for r in payload.get("recommendations", []) or []:
            if not isinstance(r, dict):
                continue
            records.append(
                MMRCRecommendation(
                    provenance=prov(r), state=state, report_year=year,
                    recommendation=r.get("recommendation", "").strip(),
                    category=r.get("category"), target_level=r.get("target_level"),
                )
            )
        return records

    _KV = re.compile(r"([A-Za-z /\-]+):\s*([^|]+)")

    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        state = doc.jurisdiction or "Unknown"
        period = None
        year = doc.report_year
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
            if low.startswith("report period:"):
                period = line.split(":", 1)[1].strip()
                continue
            if low.startswith("report year:"):
                year = _to_int(line.split(":", 1)[1]) or year
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
            if section == "findings" and ("group" in fields or "pregnancy-related deaths" in fields):
                records.append(
                    MMRCFinding(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state, report_period=period,
                        population_group=fields.get("group"),
                        pregnancy_related_deaths=_to_int(fields.get("pregnancy-related deaths")),
                        pregnancy_related_mortality_ratio=_to_float(fields.get("ratio")),
                        pct_preventable=_to_float(fields.get("preventable")),
                        leading_cause=fields.get("leading cause"),
                    )
                )
            elif section == "recommendations" and "recommendation" in fields:
                records.append(
                    MMRCRecommendation(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        state=state, report_year=year,
                        recommendation=fields["recommendation"],
                        category=fields.get("category"), target_level=fields.get("target"),
                    )
                )
        return records
