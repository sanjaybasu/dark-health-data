"""Connector for state Medicaid managed care (MCO) contracts.

Each state that buys Medicaid coverage through managed care organizations signs a
contract with them (42 CFR 438.3) and amends it most years. The contract sets what a
plan must do for members: care coordination, risk screening, community health workers,
social-needs services, behavioral health integration, value-based payment, quality
withholds, network standards, and prior-authorization time limits. States post these
contracts as long PDFs (often 300-600 pages plus exhibits and amendments), so no source
compares the terms across states or years.

This connector extracts one row per requirement in ten categories, with the verbatim
contract text in ``provenance.source_span`` so the grounding check can confirm it.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    MCO_PROVISION_CATEGORIES,
    ExtractionMethod,
    ExtractionRecord,
    MCOContractProvision,
    Provenance,
    SourceDocument,
)
from .base import CandidateDoc, Connector

UNITS = ("days", "business days", "hours", "minutes", "months", "years", "percent",
         "dollars", "miles", "members per staff", "count", "other")
OBLIGATIONS = ("must", "may", "incentive", "penalty")
DOC_TYPES = ("model contract", "signed contract", "amendment", "exhibit", "other")


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "").replace("$", ""))
    return float(m.group()) if m else None


def _to_int(value: Any) -> Optional[int]:
    f = _to_float(value)
    return int(f) if f is not None else None


def _pick(value: Any, allowed: tuple[str, ...]) -> Optional[str]:
    if not value:
        return None
    v = " ".join(str(value).strip().lower().split())
    return v if v in allowed else str(value).strip()


class MCOContractConnector(Connector):
    dataset_id = "mco_contract"
    name = "Medicaid Managed Care Contract Provisions"
    description = (
        "Requirements that state Medicaid agencies place on managed care organizations, "
        "by state, document, and year, in ten categories (care coordination, risk "
        "screening, CHWs, social needs, behavioral health, value-based payment, quality "
        "incentives, network adequacy, prior authorization, member outreach)."
    )
    version = "0.1.1"
    record_models = [MCOContractProvision]
    ensemble_fields = ["provision_category", "numeric_value", "numeric_unit"]
    identity_columns = ["state", "document_title", "section", "provision_category"]
    # About two pages per request: dense sections give many rows, so keep the output small.
    chunk_chars = 6_000

    def ensemble_key(self, record):
        if isinstance(record, MCOContractProvision):
            req = re.sub(r"\W+", " ", (record.requirement or "").lower())[:60]
            return ("mco", record.state, record.document_title, record.provenance.page_start,
                    record.provision_category, req)
        return None

    # ----- domain axioms (verification layer) -----
    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        M = MCOContractProvision

        def has_quote(r):
            if not isinstance(r, M):
                return None, ""
            return bool((r.provenance.source_span or "").strip()), "no verbatim contract text"

        def known_category(r):
            if not isinstance(r, M) or not r.provision_category:
                return None, ""
            return (r.provision_category in MCO_PROVISION_CATEGORIES,
                    f"category outside the controlled list: {r.provision_category}")

        def known_unit(r):
            if not isinstance(r, M) or not r.numeric_unit:
                return None, ""
            return r.numeric_unit in UNITS, f"unit outside the controlled list: {r.numeric_unit}"

        def grain(r):
            if not isinstance(r, M):
                return id(r)
            span = re.sub(r"\s+", " ", (r.provenance.source_span or "").lower())[:120]
            return (r.state, r.document_title, span) if span else id(r)

        def no_repeated_quote(group):
            # two rows citing the same contract text are usually one requirement read twice
            if len(group) <= 1:
                return []
            return [(r, False, f"same contract text as {len(group) - 1} other row(s)")
                    for r in group[1:]]

        return [
            RecordConstraint("has_quote", has_quote, "soft"),
            RecordConstraint("known_category", known_category, "soft"),
            RecordConstraint("known_unit", known_unit, "soft"),
            GroupConstraint("no_repeated_quote", grain, no_repeated_quote, "soft"),
        ]

    # ----- discovery -----
    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        docs: list[CandidateDoc] = []
        for c in source_entry.get("contracts", []):
            docs.append(
                CandidateDoc(
                    dataset_id=self.dataset_id,
                    url=c.get("url"),
                    local_path=c.get("local_path"),
                    title=c.get("title"),
                    publisher=source_entry.get("publisher"),
                    jurisdiction=source_entry.get("state"),
                    program=c.get("type") or source_entry.get("program"),
                    report_year=c.get("year"),
                )
            )
        return docs

    # ----- LLM extraction contract -----
    def extraction_schema(self) -> dict[str, Any]:
        s = {"type": ["string", "null"]}
        return {
            "type": "object",
            "properties": {
                "provisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "section": {**s, "description": "section number and heading, as printed"},
                            "provision_category": {"type": "string",
                                                   "enum": list(MCO_PROVISION_CATEGORIES)},
                            "requirement": {**s, "description": "one sentence: what the MCO must or may do"},
                            "target_population": {**s, "description": "members it applies to, if limited"},
                            "numeric_value": {"type": ["number", "null"]},
                            "numeric_unit": {"type": ["string", "null"], "enum": [*UNITS, None]},
                            "obligation": {"type": ["string", "null"], "enum": [*OBLIGATIONS, None]},
                            "quote": {"type": "string", "description": "verbatim contract text, at most 400 characters"},
                            "page": {"type": ["integer", "null"]},
                            "confidence": {"type": ["number", "null"]},
                        },
                        "required": ["provision_category", "requirement", "quote"],
                    },
                },
            },
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting requirements from pages of a state Medicaid managed care "
            "contract (or an amendment or exhibit) between the state and its managed care "
            "organizations (MCOs). Emit one row in `provisions` for each distinct requirement "
            "on these pages that falls in one of these categories:\n"
            "- care coordination and care management: who gets care management, staffing "
            "ratios, contact frequency, care plans, transitions of care after a hospital stay\n"
            "- health risk assessment and screening: risk assessments or screenings of new or "
            "existing members and their time limits\n"
            "- community health workers: CHWs, promotores, peer supports, doulas used by the MCO\n"
            "- health-related social needs and in lieu of services: housing, food, "
            "transportation, social-needs screening and referral, ILOS\n"
            "- behavioral health integration\n"
            "- value-based payment: required shares of payments in alternative payment models, "
            "APM levels, targets\n"
            "- quality incentives and withholds: withhold percentages, performance incentives, "
            "liquidated damages tied to quality measures\n"
            "- network adequacy and access: time and distance standards, appointment waits\n"
            "- prior authorization and utilization management: decision time limits, criteria\n"
            "- member outreach and engagement: contacting hard-to-reach or new members\n\n"
            "Rules:\n"
            "- Include a requirement only if it clearly belongs to one category. Skip tables "
            "of contents, definitions, and boilerplate. Also skip these topics, which are "
            "outside the dataset: provider credentialing and enrollment, claims payment and "
            "provider reimbursement timing, encounter data, reports or deliverables owed to "
            "the state and their due dates, readiness reviews, accreditation, financial "
            "reporting and solvency, medical loss ratio, capitation rates and actuarial "
            "methods, program integrity and fraud, member liability and billing, pharmacy "
            "benefit manager terms, call-center metrics, marketing, and grievance and appeal "
            "procedures (but keep prior-authorization decision time limits). A page with no "
            "in-scope requirement gives an empty list.\n"
            "- quote: copy the contract text the requirement rests on exactly as printed (at "
            "most 400 characters). Do not paraphrase the quote.\n"
            "- requirement: one plain sentence, e.g. 'The MCO must complete a health risk "
            "screening for each new member within 90 days of enrollment.'\n"
            "- numeric_value and numeric_unit: the requirement's number as printed (90 and "
            "'days'; 2 and 'percent' for a 2% withhold; 50 and 'members per staff' for a "
            "1:50 ratio; 12 and 'months' for an annual reassessment written as 12 months; "
            "'business days' only when the text says business or working days; 'count' for "
            "a number of things, such as 2 meetings). Never compute a number. Null when "
            "there is none.\n"
            "- obligation: 'must' for a requirement, 'may' for a permission, 'incentive' for "
            "a payment for performance, 'penalty' for a withhold, sanction, or damages.\n"
            "- section: the nearest section number and heading on the page.\n"
            "- page: the [[PAGE n]] marker where the quote appears.\n"
            "Give a 0-1 confidence for each row."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        state = doc.jurisdiction or "Unknown"
        out: list[ExtractionRecord] = []
        for p in payload.get("provisions", []) or []:
            if not isinstance(p, dict):
                continue
            quote = (p.get("quote") or "").strip() or None
            prov = Provenance(**provenance_base, page_start=p.get("page"),
                              page_end=p.get("page"), confidence=p.get("confidence"),
                              source_span=quote)
            out.append(
                MCOContractProvision(
                    provenance=prov, state=state, document_title=doc.title,
                    document_type=_pick(doc.program, DOC_TYPES),
                    contract_year=doc.report_year,
                    section=p.get("section"),
                    provision_category=_pick(p.get("provision_category"),
                                             tuple(MCO_PROVISION_CATEGORIES)),
                    requirement=p.get("requirement"),
                    target_population=p.get("target_population"),
                    numeric_value=_to_float(p.get("numeric_value")),
                    numeric_unit=_pick(p.get("numeric_unit"), UNITS),
                    obligation=_pick(p.get("obligation"), OBLIGATIONS),
                )
            )
        return out

    # ----- deterministic reference parser (canonical synthetic layout) -----
    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        """Synthetic fixture lines:
        PROVISION | page | section | category | requirement | value | unit | obligation | quote
        """
        out: list[ExtractionRecord] = []
        for raw in text.splitlines():
            if not raw.startswith("PROVISION |"):
                continue
            parts = [x.strip() for x in raw.split("|")]
            if len(parts) < 9:
                continue
            _, page, section, cat, req, val, unit, obl, quote = parts[:9]
            prov = Provenance(
                source_document_id=doc.document_id, source_url=doc.source_url,
                method=ExtractionMethod.RULE, extractor_version=self.version,
                page_start=_to_int(page), page_end=_to_int(page), source_span=quote or None,
            )
            out.append(
                MCOContractProvision(
                    provenance=prov, state=doc.jurisdiction or "Unknown",
                    document_title=doc.title, document_type=_pick(doc.program, DOC_TYPES),
                    contract_year=doc.report_year, section=section or None,
                    provision_category=cat or None, requirement=req or None,
                    numeric_value=_to_float(val), numeric_unit=unit or None,
                    obligation=obl or None,
                )
            )
        return out
