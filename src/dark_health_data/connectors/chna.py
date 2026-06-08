"""Connector for Hospital Community Health Needs Assessments (CHNAs).

Under IRC 501(r)(3) (ACA section 9007), tax-exempt hospitals must conduct a CHNA
every three years and adopt an implementation strategy, and make both public.
~95% do -- but as scattered PDFs across thousands of hospital websites, so the
identified community needs and the investments hospitals commit to are narrative
text, not analyzable data. This connector extracts identified needs (and whether
they were prioritized) and the implementation strategies addressing them.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    CHNAIdentifiedNeed,
    CHNAImplementationStrategy,
    ExtractionMethod,
    ExtractionRecord,
    Provenance,
    SourceDocument,
)
from .base import CandidateDoc, Connector

_TRUEISH = {"yes", "true", "y", "1", "priority"}


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(round(float(str(value).replace(",", ""))))
    except (ValueError, TypeError):
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value in (None, ""):
        return None
    return str(value).strip().lower() in _TRUEISH


class CHNAConnector(Connector):
    dataset_id = "chna"
    name = "Hospital Community Health Needs Assessments"
    description = (
        "Identified community health needs and implementation strategies extracted "
        "from tax-exempt hospital CHNA reports."
    )
    version = "0.1.0"
    record_models = [CHNAIdentifiedNeed, CHNAImplementationStrategy]
    ensemble_fields = ["domain", "is_priority", "priority_rank"]
    identity_columns = ["state", "hospital_name", "need", "report_year"]

    def ensemble_key(self, record):
        if isinstance(record, CHNAIdentifiedNeed):
            return ("chna_n", record.hospital_name, record.need, record.report_year)
        return None

    def constraints(self) -> list:
        from ..verify.constraints import RecordConstraint

        N = CHNAIdentifiedNeed

        def rank_positive(r):
            if not isinstance(r, N) or r.priority_rank is None:
                return None, ""
            return (r.priority_rank > 0, f"non-positive priority rank: {r.priority_rank}")

        def rank_implies_priority(r):
            # a ranked need that is explicitly marked non-priority is internally inconsistent
            if not isinstance(r, N) or r.priority_rank is None or r.is_priority is None:
                return None, ""
            return (not (r.priority_rank is not None and r.is_priority is False),
                    "need has a priority rank but is marked non-priority")

        return [
            RecordConstraint("priority_rank_positive", rank_positive, "soft"),
            RecordConstraint("rank_implies_priority", rank_implies_priority, "soft"),
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
                    publisher=rep.get("hospital") or source_entry.get("hospital"),
                    jurisdiction=rep.get("state") or source_entry.get("state"),
                    report_year=rep.get("year"),
                )
            )
        return docs

    def extraction_schema(self) -> dict[str, Any]:
        need = {
            "type": "object",
            "properties": {
                "need": {"type": "string"},
                "domain": {"type": ["string", "null"], "description": "normalized topic"},
                "is_priority": {"type": ["boolean", "null"]},
                "priority_rank": {"type": ["integer", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["need"],
        }
        strategy = {
            "type": "object",
            "properties": {
                "need_addressed": {"type": ["string", "null"]},
                "strategy": {"type": "string"},
                "measurable_objective": {"type": ["string", "null"]},
                "page": {"type": ["integer", "null"]},
                "confidence": {"type": ["number", "null"]},
            },
            "required": ["strategy"],
        }
        return {
            "type": "object",
            "properties": {
                "hospital_name": {"type": ["string", "null"]},
                "hospital_ein": {"type": ["string", "null"]},
                "state": {"type": ["string", "null"]},
                "report_year": {"type": ["integer", "null"]},
                "identified_needs": {"type": "array", "items": need},
                "implementation_strategies": {"type": "array", "items": strategy},
            },
            "required": ["identified_needs"],
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting structured data from a hospital Community Health "
            "Needs Assessment (CHNA) and its implementation strategy. Extract ONLY "
            "what is explicitly stated.\n\n"
            "Capture: (1) identified_needs -- each community health need named in the "
            "assessment, whether it was prioritized (is_priority) and its rank if "
            "ordered, and a normalized domain (e.g. 'Mental health', 'Access to "
            "care', 'Housing/SDOH', 'Chronic disease', 'Maternal/child health'). "
            "(2) implementation_strategies -- planned actions or investments, the "
            "need each addresses, and any measurable objective. Record the page and a "
            "0-1 confidence for each row. Copy the hospital name and EIN verbatim if printed."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        hospital = payload.get("hospital_name") or doc.publisher or "Unknown"
        state = payload.get("state") or doc.jurisdiction
        year = _to_int(payload.get("report_year")) or doc.report_year
        ein = payload.get("hospital_ein")
        records: list[ExtractionRecord] = []

        def prov(item: dict[str, Any]) -> Provenance:
            return Provenance(
                **provenance_base, page_start=item.get("page"), page_end=item.get("page"),
                confidence=item.get("confidence"),
            )

        for n in payload.get("identified_needs", []) or []:
            if not isinstance(n, dict):
                continue
            records.append(
                CHNAIdentifiedNeed(
                    provenance=prov(n), hospital_name=hospital, hospital_ein=ein, state=state,
                    report_year=year, need=n.get("need", "").strip(), domain=n.get("domain"),
                    is_priority=n.get("is_priority"), priority_rank=_to_int(n.get("priority_rank")),
                )
            )
        for s in payload.get("implementation_strategies", []) or []:
            if not isinstance(s, dict):
                continue
            records.append(
                CHNAImplementationStrategy(
                    provenance=prov(s), hospital_name=hospital, state=state, report_year=year,
                    need_addressed=s.get("need_addressed"), strategy=s.get("strategy", "").strip(),
                    measurable_objective=s.get("measurable_objective"),
                )
            )
        return records

    _KV = re.compile(r"([A-Za-z /]+):\s*([^|]+)")

    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        hospital = doc.publisher or "Unknown"
        state = doc.jurisdiction
        year = doc.report_year
        ein = None
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
            if low.startswith("hospital:"):
                hospital = line.split(":", 1)[1].strip()
                continue
            if low.startswith("ein:"):
                ein = line.split(":", 1)[1].strip()
                continue
            if low.startswith("state:"):
                state = line.split(":", 1)[1].strip()
                continue
            if low.startswith("report year:"):
                year = _to_int(line.split(":", 1)[1]) or year
                continue
            if line.startswith("=="):
                if "IDENTIFIED NEED" in line.upper():
                    section = "needs"
                elif "IMPLEMENTATION" in line.upper() or "STRATEG" in line.upper():
                    section = "strategies"
                else:
                    section = None
                continue
            fields = {k.strip().lower(): v.strip() for k, v in self._KV.findall(line)}
            if section == "needs" and "need" in fields:
                records.append(
                    CHNAIdentifiedNeed(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        hospital_name=hospital, hospital_ein=ein, state=state, report_year=year,
                        need=fields["need"], domain=fields.get("domain"),
                        is_priority=_to_bool(fields.get("priority")),
                        priority_rank=_to_int(fields.get("rank")),
                    )
                )
            elif section == "strategies" and "strategy" in fields:
                records.append(
                    CHNAImplementationStrategy(
                        provenance=Provenance(**base_prov, confidence=1.0),
                        hospital_name=hospital, state=state, report_year=year,
                        need_addressed=fields.get("need"), strategy=fields["strategy"],
                        measurable_objective=fields.get("objective"),
                    )
                )
        return records
