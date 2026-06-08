"""Connector for nursing-home Statements of Deficiencies (CMS Form 2567).

When state surveyors inspect a nursing home they document each deficiency on CMS
Form 2567: a federal tag (F-tag), a scope/severity letter (A-L), and a narrative
description of what the surveyor observed, paired with the facility's plan of
correction. CMS Care Compare exposes the tag, severity, and counts, but not the
narrative "why"; the full findings live in the 2567 documents. This connector
extracts the deficiencies (with their narratives) and the plans of correction.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    ExtractionMethod,
    ExtractionRecord,
    NursingHomeDeficiency,
    NursingHomePlanOfCorrection,
    NURSING_HOME_SCOPE_SEVERITY,
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


class NursingHome2567Connector(Connector):
    dataset_id = "nursing_home_2567"
    name = "Nursing Home Statements of Deficiencies (CMS Form 2567)"
    description = (
        "Cited deficiencies (federal tag, scope/severity, and the surveyor's narrative "
        "finding) and plans of correction extracted from nursing-home CMS-2567 statements "
        "of deficiencies -- the narrative 'why' that Care Compare's tag/severity counts omit."
    )
    version = "0.1.0"
    record_models = [NursingHomeDeficiency, NursingHomePlanOfCorrection]
    ensemble_fields = ["ftag", "scope_severity", "ftag_description"]
    identity_columns = ["state", "ccn", "facility_name", "ftag", "report_year"]

    def ensemble_key(self, record):
        if isinstance(record, NursingHomeDeficiency):
            return ("nh_def", record.state, record.ccn or record.facility_name,
                    record.ftag, record.report_year)
        return None

    # ----- domain axioms (verification layer) -----
    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        D = NursingHomeDeficiency

        def valid_scope_severity(r):
            if not isinstance(r, D) or not r.scope_severity:
                return None, ""
            return (r.scope_severity.strip().upper() in NURSING_HOME_SCOPE_SEVERITY,
                    f"scope_severity not a valid A-L letter: {r.scope_severity}")

        def grain(r):
            if isinstance(r, D):
                return (r.state, r.ccn or r.facility_name, r.ftag, r.report_year)
            return id(r)

        def duplicate_grain(group):
            if len(group) <= 1:
                return []
            return [(r, False, f"duplicate deficiency grain (x{len(group)})") for r in group]

        return [
            RecordConstraint("valid_scope_severity", valid_scope_severity, "soft"),
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
                    publisher=rep.get("facility") or source_entry.get("facility"),
                    jurisdiction=rep.get("state") or source_entry.get("state"),
                    report_year=rep.get("year"),
                )
            )
        return docs

    # ----- LLM extraction contract -----
    def extraction_schema(self) -> dict[str, Any]:
        deficiency = {
            "type": "object",
            "properties": {
                "ftag": {"type": ["string", "null"], "description": "federal tag, e.g. 'F689'"},
                "ftag_description": {"type": ["string", "null"], "description": "the tag's regulatory title"},
                "scope_severity": {"type": ["string", "null"], "description": "single scope/severity letter A-L"},
                "deficiency_description": {"type": ["string", "null"], "description": "surveyor's narrative finding"},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
        }
        poc = {
            "type": "object",
            "properties": {
                "ftag": {"type": ["string", "null"]},
                "correction": {"type": "string"},
                "completion_date": {"type": ["string", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["correction"],
        }
        return {
            "type": "object",
            "properties": {
                "facility_name": {"type": ["string", "null"]},
                "ccn": {"type": ["string", "null"], "description": "CMS Certification Number if printed"},
                "state": {"type": ["string", "null"]},
                "survey_date": {"type": ["string", "null"]},
                "report_year": {"type": ["integer", "null"]},
                "deficiencies": {"type": "array", "items": deficiency},
                "plans_of_correction": {"type": "array", "items": poc},
            },
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting structured data from a nursing-home Statement of "
            "Deficiencies (CMS Form 2567). Extract ONLY values explicitly present.\n\n"
            "Capture two kinds of records:\n"
            "1. deficiencies -- one row per cited deficiency. Record the federal tag "
            "(e.g. 'F689'), the tag's regulatory title, the scope/severity as a single "
            "letter A-L, and the surveyor's narrative finding (what was observed). Copy "
            "the finding text faithfully; do not summarise away specifics.\n"
            "2. plans_of_correction -- the facility's corrective action for a deficiency, "
            "the tag it addresses, and the completion date if stated.\n\n"
            "Record the page and a 0-1 confidence for each row."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        facility = payload.get("facility_name") or doc.publisher or "Unknown facility"
        state = doc.jurisdiction or payload.get("state") or "Unknown"
        ccn = payload.get("ccn")
        survey_date = payload.get("survey_date")
        year = payload.get("report_year") or doc.report_year
        records: list[ExtractionRecord] = []

        def prov(item: dict[str, Any]) -> Provenance:
            return Provenance(
                **provenance_base, page_start=item.get("page"), page_end=item.get("page"),
                confidence=item.get("confidence"),
            )

        for d in payload.get("deficiencies", []) or []:
            if not isinstance(d, dict):
                continue
            records.append(
                NursingHomeDeficiency(
                    provenance=prov(d), facility_name=facility, ccn=ccn, state=state,
                    survey_date=survey_date, report_year=year, ftag=d.get("ftag"),
                    ftag_description=d.get("ftag_description"),
                    scope_severity=d.get("scope_severity"),
                    deficiency_description=d.get("deficiency_description"),
                )
            )
        for p in payload.get("plans_of_correction", []) or []:
            if not isinstance(p, dict):
                continue
            records.append(
                NursingHomePlanOfCorrection(
                    provenance=prov(p), facility_name=facility, state=state, report_year=year,
                    ftag=p.get("ftag"), correction=p.get("correction", "").strip(),
                    completion_date=p.get("completion_date"),
                )
            )
        return records

    # ----- deterministic reference parser (canonical text layout) -----
    _KV = re.compile(r"([A-Za-z /\-]+):\s*([^|]+)")

    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        facility = doc.publisher or "Unknown facility"
        state = doc.jurisdiction or "Unknown"
        ccn: Optional[str] = None
        survey_date: Optional[str] = None
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
            if low.startswith("facility:"):
                facility = line.split(":", 1)[1].strip()
                continue
            if low.startswith("ccn:"):
                ccn = line.split(":", 1)[1].strip()
                continue
            if low.startswith("state:"):
                state = line.split(":", 1)[1].strip()
                continue
            if low.startswith("survey date:"):
                survey_date = line.split(":", 1)[1].strip()
                continue
            if low.startswith("report year:"):
                year = _to_int(line.split(":", 1)[1]) or year
                continue
            if line.startswith("=="):
                if "DEFICIENC" in line.upper():
                    section = "deficiencies"
                elif "CORRECTION" in line.upper():
                    section = "poc"
                else:
                    section = None
                continue
            fields = {k.strip().lower(): v.strip() for k, v in self._KV.findall(line)}
            if section == "deficiencies" and ("f-tag" in fields or "finding" in fields):
                records.append(
                    NursingHomeDeficiency(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        facility_name=facility, ccn=ccn, state=state, survey_date=survey_date,
                        report_year=year, ftag=fields.get("f-tag"),
                        ftag_description=fields.get("description"),
                        scope_severity=fields.get("scope/severity"),
                        deficiency_description=fields.get("finding"),
                    )
                )
            elif section == "poc" and "correction" in fields:
                records.append(
                    NursingHomePlanOfCorrection(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        facility_name=facility, state=state, report_year=year,
                        ftag=fields.get("tag"), correction=fields["correction"],
                        completion_date=fields.get("completion"),
                    )
                )
        return records
