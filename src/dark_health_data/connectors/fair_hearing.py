"""Connector for state Medicaid fair-hearing decisions.

A Medicaid applicant or member who disputes a denial, termination, or reduction can
ask for a fair hearing (42 CFR 431 Subpart E). Some states post the redacted
decisions: Connecticut (a searchable DSS list since a 2019 settlement), Minnesota,
Nebraska, Texas, and others. Each decision is a short legal document with a standard
structure (issue, findings of fact, conclusions of law, decision or order), but no
state publishes the decisions as data. This connector extracts one case-level row per
decision: what was appealed, against whom, the legal basis, and who won.

Privacy: the sources are public and state-redacted. The schema has no field for the
appellant's identity, and the instructions keep names, ages, birth dates, addresses, and
identification numbers out of free text. A soft constraint flags free text that still
looks identifying. Clinical facts the decision states are kept as written.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    FAIR_HEARING_OUTCOMES,
    ExtractionMethod,
    ExtractionRecord,
    FairHearingDecision,
    Provenance,
    SourceDocument,
)
from .base import CandidateDoc, Connector

# Free text that identifies a person: a personal title before a capitalized word, an
# age, a date of birth, or an identification number. The decisions are public and
# redacted by the state; clinical facts the decision states (a score, a diagnosis, a care
# need) are kept as written, because they carry the reason for the outcome.
_IDENTIFYING = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+|\b\d{1,3}[- ]years?[- ]old\b"
    r"|\b(?:born|date of birth|DOB)\b[^.;]{0,20}\d|\b(?:client|case|SSN|ID)\s*(?:#|no\.?|number)\s*\d", re.I
)
ACTION_TYPES = (
    "denial", "termination", "reduction", "suspension", "prior authorization denial",
    "discharge", "overpayment", "calculation", "penalty", "effective date", "other",
)
ISSUE_CATEGORIES = (
    "eligibility - income", "eligibility - assets or transfer",
    "eligibility - procedural or verification", "eligibility - other",
    "covered service - medical necessity", "covered service - other",
    "nursing facility discharge", "other",
)
# Labels the model sometimes writes for a listed value.
_ACTION_ALIASES = {"discontinuance": "termination", "discontinuation": "termination",
                   "closure": "termination", "spenddown": "calculation",
                   "applied income": "calculation", "transfer penalty": "penalty",
                   "spend-down": "calculation", "spend down": "calculation"}
_FREE_TEXT = ("issue_statement", "outcome_reason", "remedy_ordered", "service_at_issue")


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
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "").replace("$", ""))
    return float(m.group()) if m else None


def _norm_outcome(value: Any) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip().lower()
    return v if v in FAIR_HEARING_OUTCOMES else str(value).strip()


def _vocab(value: Any, allowed: tuple[str, ...], aliases: dict[str, str]) -> Optional[str]:
    """Map a label onto the controlled list; an unlisted label is kept as written (and
    fails the soft `controlled_vocabulary` check) rather than guessed."""
    if not value:
        return None
    v = " ".join(str(value).strip().lower().split())
    v = aliases.get(v, v)
    return v if v in allowed else str(value).strip()


def _join(value: Any) -> Optional[str]:
    if value in (None, "", []):
        return None
    if isinstance(value, list):
        return "; ".join(str(x).strip() for x in value if str(x).strip()) or None
    return str(value).strip()


class FairHearingConnector(Connector):
    dataset_id = "fair_hearing"
    name = "Medicaid Fair-Hearing Decisions"
    description = (
        "Case-level outcomes of state Medicaid fair hearings: program, respondent, action "
        "appealed, issue category, legal authorities, and outcome, extracted from the "
        "redacted decisions that states publish."
    )
    version = "0.1.6"
    record_models = [FairHearingDecision]
    ensemble_fields = ["outcome", "issue_category", "action_type"]
    identity_columns = ["state", "decision_id"]
    # A decision is one case: the issue is on page 1-2 and the order on the last page,
    # so the whole document goes in one request. Output is one short row, so the
    # 8,192-token cap is not at risk.
    chunk_chars = 120_000

    def ensemble_key(self, record):
        if isinstance(record, FairHearingDecision):
            return ("fh", record.state, record.decision_id)
        return None

    # ----- domain axioms (verification layer) -----
    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        F = FairHearingDecision

        def known_outcome(r):
            if not isinstance(r, F) or not r.outcome:
                return None, ""
            return (r.outcome.strip().lower() in FAIR_HEARING_OUTCOMES,
                    f"unrecognized outcome: {r.outcome}")

        def no_identifying_text(r):
            if not isinstance(r, F):
                return None, ""
            hits = [f for f in _FREE_TEXT if _IDENTIFYING.search(getattr(r, f) or "")]
            return (not hits, f"possible identifying text in: {', '.join(hits)}")

        def controlled_vocabulary(r):
            if not isinstance(r, F):
                return None, ""
            bad = [f"{k}={v}" for k, v, allowed in
                   (("action_type", r.action_type, ACTION_TYPES),
                    ("issue_category", r.issue_category, ISSUE_CATEGORIES))
                   if v and v not in allowed]
            return (not bad, f"value outside the controlled list: {', '.join(bad)}")

        def grain(r):
            return (r.state, r.decision_id) if isinstance(r, F) else id(r)

        def one_row_per_decision(group):
            if len(group) <= 1:
                return []
            return [(r, False, f"more than one row for this decision (x{len(group)})")
                    for r in group]

        return [
            RecordConstraint("known_outcome", known_outcome, "soft"),
            RecordConstraint("no_identifying_text", no_identifying_text, "soft"),
            RecordConstraint("controlled_vocabulary", controlled_vocabulary, "soft"),
            GroupConstraint("one_row_per_decision", grain, one_row_per_decision, "soft"),
        ]

    # ----- discovery -----
    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        docs: list[CandidateDoc] = []
        for dec in source_entry.get("decisions", []):
            docs.append(
                CandidateDoc(
                    dataset_id=self.dataset_id,
                    url=dec.get("url"),
                    local_path=dec.get("local_path"),
                    title=dec.get("id"),
                    publisher=source_entry.get("publisher"),
                    jurisdiction=source_entry.get("state"),
                    program=dec.get("category") or source_entry.get("category"),
                    report_year=dec.get("year"),
                )
            )
        return docs

    # ----- LLM extraction contract -----
    def extraction_schema(self) -> dict[str, Any]:
        s = {"type": ["string", "null"]}
        return {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "decision_year": {"type": ["integer", "null"]},
                            "program": {**s, "description": "program or coverage group, e.g. 'HUSKY D', 'Long-term care Medicaid', 'Community First Choice'"},
                            "respondent": {**s, "description": "entity whose action was appealed, as named"},
                            "respondent_type": {**s, "description": "state agency / managed care organization / administrative services organization / nursing facility / marketplace / other"},
                            "action_type": {"type": ["string", "null"], "enum": [*ACTION_TYPES, None]},
                            "issue_category": {"type": ["string", "null"], "enum": [*ISSUE_CATEGORIES, None]},
                            "service_at_issue": {**s, "description": "benefit or service in dispute, if any"},
                            "issue_statement": {**s, "description": "the decision's statement of the issue, without personal details"},
                            "outcome": {"type": ["string", "null"], "enum": ["granted", "denied", "granted in part", "remanded", "dismissed", "withdrawn", "other", None], "description": "the appellant's result"},
                            "outcome_reason": {**s, "description": "one-sentence reason for the outcome, without personal details"},
                            "remedy_ordered": {**s, "description": "what the order directs the respondent to do, if anything"},
                            "amount_at_issue": {"type": ["number", "null"], "description": "dollar amount in dispute, if printed"},
                            "legal_authorities": {"type": "array", "items": {"type": "string"}, "description": "statutes, regulations, and policy sections cited, e.g. '42 CFR 441.500', 'Conn. Gen. Stat. 17b-261'"},
                            "hearing_officer": {**s, "description": "name of the presiding hearing officer or judge"},
                            "page": {"type": ["integer", "null"], "description": "page of the decision or order"},
                            "confidence": {"type": ["number", "null"]},
                        },
                    },
                },
            },
        }

    def extraction_instructions(self) -> str:
        return (
            "You are extracting structured data from one state Medicaid fair-hearing "
            "decision (an administrative appeal by an applicant, member, or resident). "
            "Emit exactly one row in `decisions` for the document. Extract only what the "
            "decision states.\n\n"
            "Fields:\n"
            "- respondent: the entity whose action was appealed (the state agency, a "
            "managed care organization, an administrative services organization such as a "
            "dental or behavioral health ASO, a nursing facility, or the marketplace).\n"
            "- action_type and issue_category: use exactly one of the listed values. When "
            "several issues apply, use the main one. For action_type:\n"
            "  'prior authorization denial': the respondent denied a request to approve a "
            "specific service, item, drug, or level of care before it is given (prior or "
            "service authorization of orthodontia, a drug, equipment, home care hours, or a "
            "nursing-facility level-of-care request). Use it even when the decision just "
            "says 'denied the request'. A decision on an application for Medicaid coverage, "
            "on a spousal asset or income allowance, or on a spend-down is never a prior "
            "authorization denial.\n"
            "  'denial': an application for coverage (including long-term care coverage) "
            "was denied.\n"
            "  'termination': existing coverage was discontinued or closed.\n"
            "  'reduction': an existing service, budget, or number of hours was cut "
            "(including a cut to zero), even if the cut came from a new assessment or budget.\n"
            "  'discharge': a nursing facility's involuntary discharge or transfer.\n"
            "  'effective date': the dispute is about when coverage starts.\n"
            "  'calculation': the dispute is about an applied-income, spenddown, premium, "
            "or tax-credit amount, not a cut to a service.\n"
            "  'penalty': a transfer-of-assets penalty period.\n"
            "- outcome: the appellant's result, read from the DECISION or ORDER section. "
            "Apply these rules in order:\n"
            "  1. If that section states the appeal is GRANTED or SUSTAINED, DENIED, "
            "REMANDED, DISMISSED, or WITHDRAWN, use that word ('sustained' is 'granted'), "
            "even when the body of the decision reads differently or the order lists "
            "corrective steps after a remand.\n"
            "  2. If the hearing officer finds the issue moot (for example, the respondent "
            "approved the request before the decision), use 'dismissed'.\n"
            "  3. Otherwise judge the order: 'granted' when the respondent must reverse its "
            "action or reopen, rescreen, redetermine, recompute, or restore; 'denied' when "
            "its action stands; 'granted in part' when some but not all issues are "
            "sustained; 'other' when none fits.\n"
            "  Whatever the order's wording, use 'granted in part' when the decision grants "
            "at least one issue and denies at least one other.\n"
            "- respondent: if the name is redacted, give only the type (e.g. 'nursing "
            "facility').\n"
            "- amount_at_issue: only a dollar amount that the appellant is contesting and "
            "that the decision prints as such: an overpayment or claim against the "
            "appellant, a patient liability or charge the appellant disputes, a denied "
            "payment, a spenddown or premium the appellant disputes. Never use the "
            "appellant's income, an asset total, an income or asset limit, or another "
            "figure from the eligibility computation. Copy it; never compute. Null when "
            "unsure.\n"
            "- legal_authorities: each statute, regulation, or policy-manual section cited "
            "in the conclusions of law, as printed. Leave out citations that appear only in "
            "the standard notice of reconsideration or appeal rights.\n"
            "- hearing_officer: the name of the hearing officer or judge who signed.\n\n"
            "Privacy rule: never copy the appellant's or any family member's name, age, "
            "date of birth, address, or identification number into any field. The reason "
            "may state the coverage criterion and the finding the decision rests on (for "
            "example, a score against a threshold). Write the issue statement and "
            "reason in general terms ('the Appellant', 'the child'). Do not name anyone "
            "other than the hearing officer.\n\n"
            "Record the page of the decision or order and a 0-1 confidence."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        state = doc.jurisdiction or "Unknown"
        decision_id = doc.title or doc.document_id
        records: list[ExtractionRecord] = []
        for d in payload.get("decisions", []) or []:
            if not isinstance(d, dict):
                continue
            prov = Provenance(**provenance_base, page_start=d.get("page"),
                              page_end=d.get("page"), confidence=d.get("confidence"))
            records.append(
                FairHearingDecision(
                    provenance=prov, state=state, decision_id=decision_id,
                    decision_category=doc.program,
                    decision_year=_to_int(d.get("decision_year")) or doc.report_year,
                    program=d.get("program"), respondent=d.get("respondent"),
                    respondent_type=d.get("respondent_type"),
                    action_type=_vocab(d.get("action_type"), ACTION_TYPES, _ACTION_ALIASES),
                    issue_category=_vocab(d.get("issue_category"), ISSUE_CATEGORIES, {}),
                    service_at_issue=d.get("service_at_issue"),
                    issue_statement=d.get("issue_statement"),
                    outcome=_norm_outcome(d.get("outcome")),
                    outcome_reason=d.get("outcome_reason"),
                    remedy_ordered=d.get("remedy_ordered"),
                    amount_at_issue=_to_float(d.get("amount_at_issue")),
                    legal_authorities=_join(d.get("legal_authorities")),
                    hearing_officer=d.get("hearing_officer"),
                )
            )
        return records

    # ----- deterministic reference parser (canonical synthetic layout) -----
    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        fields: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if ":" not in line or line.startswith("[[PAGE"):
                continue
            key, val = line.split(":", 1)
            fields[key.strip().lower().replace(" ", "_")] = val.strip()
        if "outcome" not in fields:
            return []
        prov = Provenance(
            source_document_id=doc.document_id, source_url=doc.source_url,
            method=ExtractionMethod.RULE, extractor_version=self.version,
        )
        return [
            FairHearingDecision(
                provenance=prov, state=fields.get("state") or doc.jurisdiction or "Unknown",
                decision_id=fields.get("decision_id") or doc.title or doc.document_id,
                decision_category=fields.get("category") or doc.program,
                decision_year=_to_int(fields.get("decision_year")) or doc.report_year,
                program=fields.get("program"), respondent=fields.get("respondent"),
                respondent_type=fields.get("respondent_type"),
                action_type=fields.get("action_type"),
                issue_category=fields.get("issue_category"),
                service_at_issue=fields.get("service_at_issue"),
                issue_statement=fields.get("issue"),
                outcome=_norm_outcome(fields.get("outcome")),
                outcome_reason=fields.get("outcome_reason"),
                remedy_ordered=fields.get("remedy_ordered"),
                amount_at_issue=_to_float(fields.get("amount_at_issue")),
                legal_authorities=fields.get("legal_authorities"),
                hearing_officer=fields.get("hearing_officer"),
            )
        ]
