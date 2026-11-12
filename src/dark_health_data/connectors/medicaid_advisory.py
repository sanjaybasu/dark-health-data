"""Connector for the meeting minutes of state Medicaid advisory bodies.

Every state Medicaid agency must have a Medical Care Advisory Committee (42 CFR 431.12).
The 2024 access rule renamed it the Medicaid Advisory Committee and added a Beneficiary
Advisory Council of people with Medicaid, and it requires states to post the minutes.
The minutes record what members and the public raise, what the committee recommends,
and how the agency answers. States post them one PDF or web page at a time, and no
source codes them across states (the last cross-state coding used 1979 minutes).

This connector extracts one row per discussion item: its type (a member concern, a
public comment, a recommendation, a motion, an agency response or update), its topic
in a controlled list, the speaker's role, a one-sentence summary, the agency's reply,
and the verbatim minutes text in ``provenance.source_span``.

Privacy: the minutes are public, but beneficiaries speak in them. Rows give people by
role only. The instructions keep names, ages, addresses, and identification numbers out
of every field, including the quote, and a soft constraint flags text that still looks
identifying.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models import (
    ADVISORY_COMMITTEE_TYPES,
    ADVISORY_ITEM_TYPES,
    ADVISORY_SPEAKER_ROLES,
    ADVISORY_TOPICS,
    AdvisoryMeetingItem,
    ExtractionMethod,
    ExtractionRecord,
    Provenance,
    SourceDocument,
)
from .base import CandidateDoc, Connector

# A personal title before a capitalized word, an age, a birth date, or an ID number.
_IDENTIFYING = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+|\b\d{1,3}[- ]years?[- ]old\b"
    r"|\b(?:Director|Commissioner|Secretary|Chair|Chairman|Chairwoman|Senator|Representative"
    r"|Delegate|Deputy)\s+(?!(?:Secretary|Commissioner|Director|Administrator|Chair|Medicaid"
    r"|Finance|Executive|Chief|General|Medical|Assistant|No)\b)"
    r"[A-Z][a-z]+\b(?![ ]+(?:of|for)\b)"
    r"|\bturn(?:ed|ing|s)\s+(?:age\s+)?\d{1,3}\b"
    r"|\b(?:born|date of birth|DOB)\b[^.;]{0,20}\d|\b(?:case|SSN|ID|Medicaid)\s*(?:#|no\.?|number)\s*\d"
)
_FREE_TEXT = ("summary", "agency_response")

# A quote that opens with the speaker: "Ms. Keen provided ...", "Director Ford, DMAS,
# reviewed ...", "Janice Tufte: Jails are ...", "Lee Guice asked ...". The model is told to
# start after the name but does not always; the speaker part is cut, and what is left is
# still verbatim source text, so grounding still holds.
_NAME = r"[A-Z][A-Za-z'\u2019.-]+"
_TITLE = (r"(?:(?:Mr|Mrs|Ms|Miss|Dr)\.?|Director|Commissioner|Secretary|Chair|Chairman|"
          r"Chairwoman|Senator|Representative|Delegate|Deputy|Deputy Secretary|"
          r"Deputy Commissioner)")
_VERBS = (r"(?:asked|said|says|stated|states|provided|presented|reviewed|discussed|noted|"
          r"indicated|reported|explained|recommended|requested|gave|shared|commented|"
          r"expressed|reminded|announced|updated|acknowledged|moved|seconded|called|"
          r"thanked|inquired|voiced|raised|mentioned|added|agreed|responded|replied|"
          r"clarified|suggested|confirmed|described|introduced|welcomed|opened|summarized|"
          r"went|also|then|led|offered|emphasized|highlighted|questioned|wanted|wondered|"
          r"spoke|talked|answered|motioned|made|proposed|urged|encouraged|shed|wants|"
          r"provide|present|review|give|share|discuss|note|report|ask|say)\b")
_SPEAKER = re.compile(
    rf"^\s*(?:(?:Motion|Action|Comment|Question|Response)\s*:\s*)?"
    rf"(?!(?:The|A|An|This|That|These|Those|It|They|We|He|She|Members?|Staff|Committee|"
    rf"Council|Department|Agency|Division|Medicaid|State|Plans?|MCOs?|Providers?)\b)"
    rf"(?:{_TITLE}\s+)?{_NAME}(?:\s+{_NAME}){{0,2}}"
    rf"(?:\s*:\s+|(?:,\s*[^,:]{{0,80}}?,?)?\s+(?={_VERBS}))")


def _trim_speaker(quote: Optional[str]) -> Optional[str]:
    """Cut a leading speaker name (and title, affiliation) off a verbatim quote."""
    if not quote:
        return quote
    m = _SPEAKER.match(quote)
    if not m:
        return quote
    rest = quote[m.end():].strip()
    # keep the original if the cut would leave almost nothing
    return rest if len(rest) >= 15 else quote
VOTE_RESULTS = ("passed", "failed", "tabled")
# Meeting business the instructions skip; a row that reads like it is flagged, not dropped.
_PROCEDURAL = re.compile(
    r"\b(?:approv|accept)\w*\b[^.]{0,40}\b(?:minutes|agenda|meeting summary)\b"
    r"|\bwill meet (?:quarterly|monthly|annually|every)\b|\bno (?:public )?comments? (?:were|was)\b|\bcorrection\w*\b[^.]{0,40}"
    r"\b(?:minutes|meeting summary)\b|\bwelcom\w*\b|\bintroduc\w+ (?:themselves|members|attendees)"
    r"|\bintroductions\b|\badjourn\w*|\broll call\b|\bquorum\b"
    r"|\b(?:next|future|upcoming) meetings?\b[^.]{0,60}\b(?:date|time|format|virtual|in-person"
    r"|hybrid|schedul\w*|length)\b", re.I)


def _to_int(value: Any) -> Optional[int]:
    m = re.search(r"\d{4}", str(value or ""))
    return int(m.group()) if m else None


def _pick(value: Any, allowed: tuple[str, ...]) -> Optional[str]:
    if not value:
        return None
    v = " ".join(str(value).strip().lower().split())
    return v if v in allowed else str(value).strip()


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _date(value: Any) -> Optional[str]:
    """YYYY-MM-DD from '2025-09-15' or 'September 15, 2025'; else None."""
    text = str(value or "")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", text)
        if not m or m.group(1).lower() not in _MONTHS:
            return None
        y, mo, d = int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else None


class MedicaidAdvisoryConnector(Connector):
    dataset_id = "medicaid_advisory"
    name = "Medicaid Advisory Committee and Beneficiary Council Minutes"
    description = (
        "Discussion items from the posted minutes of state Medicaid advisory committees "
        "and beneficiary advisory councils: item type, topic, speaker role, summary, and "
        "agency response, with the verbatim minutes text."
    )
    version = "0.1.1"
    record_models = [AdvisoryMeetingItem]
    ensemble_fields = ["item_type", "topic_category", "speaker_role"]
    identity_columns = ["state", "committee_name", "meeting_date"]
    # Most minutes are 3-20 pages, so most go in one request. Rows are short, so a long
    # meeting stays well under the output cap.
    chunk_chars = 40_000

    def finalize(self, records: list) -> list:
        """Give every row of a document the meeting date that the document states.

        A long set of minutes is split across requests and only page 1 prints the date.
        When the rows of one document carry exactly one date, rows without it get it.
        """
        dates: dict[str, set] = {}
        for r in records:
            if isinstance(r, AdvisoryMeetingItem) and r.meeting_date:
                dates.setdefault(r.provenance.source_document_id, set()).add(r.meeting_date)
        for r in records:
            if isinstance(r, AdvisoryMeetingItem) and not r.meeting_date:
                found = dates.get(r.provenance.source_document_id, set())
                if len(found) == 1:
                    r.meeting_date = next(iter(found))
                    r.meeting_year = int(r.meeting_date[:4])
        return records

    def ensemble_key(self, record):
        if isinstance(record, AdvisoryMeetingItem):
            s = re.sub(r"\W+", " ", (record.summary or "").lower())[:60]
            return ("adv", record.state, record.committee_name, record.meeting_date,
                    record.provenance.page_start, s)
        return None

    # ----- domain axioms (verification layer) -----
    def constraints(self) -> list:
        from ..verify.constraints import GroupConstraint, RecordConstraint

        A = AdvisoryMeetingItem

        def has_quote(r):
            if not isinstance(r, A):
                return None, ""
            return bool((r.provenance.source_span or "").strip()), "no verbatim minutes text"

        def controlled_vocabulary(r):
            if not isinstance(r, A):
                return None, ""
            bad = [f"{k}={v}" for k, v, allowed in
                   (("committee_type", r.committee_type, ADVISORY_COMMITTEE_TYPES),
                    ("item_type", r.item_type, ADVISORY_ITEM_TYPES),
                    ("topic_category", r.topic_category, ADVISORY_TOPICS),
                    ("speaker_role", r.speaker_role, ADVISORY_SPEAKER_ROLES),
                    ("vote_result", r.vote_result, VOTE_RESULTS))
                   if v and v not in allowed]
            return (not bad, f"value outside the controlled list: {', '.join(bad)}")

        def no_identifying_text(r):
            if not isinstance(r, A):
                return None, ""
            texts = {f: getattr(r, f) for f in _FREE_TEXT}
            texts["quote"] = r.provenance.source_span
            hits = [f for f, t in texts.items() if _IDENTIFYING.search(t or "")]
            return (not hits, f"possible identifying text in: {', '.join(hits)}")

        def not_procedural(r):
            if not isinstance(r, A):
                return None, ""
            return (not _PROCEDURAL.search(r.summary or ""),
                    "reads like meeting business (minutes approval, welcome, scheduling)")

        def has_date(r):
            if not isinstance(r, A):
                return None, ""
            return bool(r.meeting_date), "no meeting date"

        def grain(r):
            if not isinstance(r, A):
                return id(r)
            span = re.sub(r"\s+", " ", (r.provenance.source_span or "").lower())[:120]
            return (r.state, r.committee_name, r.meeting_date, span) if span else id(r)

        def no_repeated_quote(group):
            if len(group) <= 1:
                return []
            return [(r, False, f"same minutes text as {len(group) - 1} other row(s)")
                    for r in group[1:]]

        return [
            RecordConstraint("has_quote", has_quote, "soft"),
            RecordConstraint("controlled_vocabulary", controlled_vocabulary, "soft"),
            RecordConstraint("no_identifying_text", no_identifying_text, "soft"),
            RecordConstraint("has_date", has_date, "soft"),
            RecordConstraint("not_procedural", not_procedural, "soft"),
            GroupConstraint("no_repeated_quote", grain, no_repeated_quote, "soft"),
        ]

    # ----- discovery -----
    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        docs: list[CandidateDoc] = []
        for m in source_entry.get("meetings", []):
            docs.append(
                CandidateDoc(
                    dataset_id=self.dataset_id,
                    url=m.get("url"),
                    local_path=m.get("local_path"),
                    title=m.get("title"),
                    publisher=source_entry.get("publisher"),
                    jurisdiction=source_entry.get("state"),
                    program=m.get("committee") or source_entry.get("committee"),
                    report_year=m.get("year"),
                )
            )
        return docs

    # ----- LLM extraction contract -----
    def extraction_schema(self) -> dict[str, Any]:
        s = {"type": ["string", "null"]}
        return {
            "type": "object",
            "properties": {
                "meeting": {
                    "type": "object",
                    "properties": {
                        "committee_name": {**s, "description": "as printed"},
                        "committee_type": {"type": ["string", "null"],
                                           "enum": [*ADVISORY_COMMITTEE_TYPES, None]},
                        "meeting_date": {**s, "description": "YYYY-MM-DD"},
                    },
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_type": {"type": "string", "enum": list(ADVISORY_ITEM_TYPES)},
                            "topic_category": {"type": "string", "enum": list(ADVISORY_TOPICS)},
                            "speaker_role": {"type": "string",
                                             "enum": list(ADVISORY_SPEAKER_ROLES)},
                            "summary": {**s, "description": "one sentence, people by role only"},
                            "agency_response": s,
                            "vote_result": {"type": ["string", "null"],
                                            "enum": [*VOTE_RESULTS, None]},
                            "quote": {"type": "string",
                                      "description": "verbatim minutes text, at most 300 characters, no names"},
                            "page": {"type": ["integer", "null"]},
                            "confidence": {"type": ["number", "null"]},
                        },
                        "required": ["item_type", "topic_category", "speaker_role",
                                     "summary", "quote"],
                    },
                },
            },
            "required": ["items"],
        }

    def extraction_instructions(self) -> str:
        return (
            "You are reading the minutes of a meeting of a state Medicaid advisory body: "
            "the Medical Care Advisory Committee or Medicaid Advisory Committee (MAC), the "
            "Beneficiary Advisory Council (BAC), a joint meeting, or one of their "
            "subcommittees.\n"
            "In `meeting`, give the committee name as printed, its type, and the meeting "
            "date as YYYY-MM-DD. The date is usually at the top of the first page (for "
            "example 'September 15, 2025, Minutes'); give it whenever these pages print it.\n"
            "In `items`, give one row for each discussion item on these pages:\n"
            "- member concern or comment: a committee or council member raises a problem, "
            "a question, or an experience\n"
            "- public comment: a person who is not a member speaks in the public comment "
            "period\n"
            "- recommendation: the committee or a member recommends that the agency do "
            "something\n"
            "- motion or vote: a motion on a substantive matter, with vote_result passed, "
            "failed, or tabled\n"
            "- agency response: the agency answers a concern or recommendation, or commits "
            "to an action\n"
            "- agency update: the agency or a plan presents a program, a policy, data, or "
            "a change (one row per presentation, not per slide)\n"
            "Skip roll call, quorum, approval of earlier minutes or of the agenda (and the "
            "motions and votes to approve them), introductions, the date, length, or format "
            "of future meetings, and adjournment.\n\n"
            "Rules:\n"
            "- topic_category: the best of the controlled list. 'eligibility, enrollment, "
            "and renewals' covers applications, redeterminations, unwinding, and work "
            "requirements; 'benefits, coverage, and waivers' covers covered services, state "
            "plan amendments, and 1115 or 1915 waivers; 'committee operations' covers "
            "membership, recruitment, bylaws, and how the committee works.\n"
            "- speaker_role: the role of the person who speaks or acts, as the minutes "
            "state it. 'beneficiary or family member' only when the minutes say the person "
            "has Medicaid or cares for someone who does. A member of the committee whose "
            "role is not stated is 'committee member'.\n"
            "- summary: one plain sentence. Name people by role only ('a council member', "
            "'agency staff', 'a parent of a member'). Never give a person's name, age, "
            "address, or ID number. Keep facts about services, programs, plans, and places "
            "(counties, cities).\n"
            "- agency_response: the agency's reply or commitment to this item if the "
            "minutes record one, in one sentence; otherwise null.\n"
            "- quote: copy one continuous span of the minutes that the row rests on, exactly "
            "as printed, at most 300 characters. Do not join pieces with '...'. The quote "
            "must not contain any person's name: start it after the speaker's name, title, "
            "and affiliation. For 'Ms. Lee, Iowa CareGivers, asked whether rates will rise' "
            "the quote is 'asked whether rates will rise'.\n"
            "- page: the [[PAGE n]] marker where the quote appears.\n"
            "Give a 0-1 confidence for each row. A page with no discussion item gives an "
            "empty list."
        )

    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        state = doc.jurisdiction or "Unknown"
        meeting = payload.get("meeting") or {}
        if not isinstance(meeting, dict):
            meeting = {}
        # the posting's own date (from discovery) wins over a date read from the text
        date = _date(doc.title) or _date(meeting.get("meeting_date"))
        out: list[ExtractionRecord] = []
        for it in payload.get("items", []) or []:
            if not isinstance(it, dict):
                continue
            quote = _trim_speaker((it.get("quote") or "").strip() or None)
            prov = Provenance(**provenance_base, page_start=it.get("page"),
                              page_end=it.get("page"), confidence=it.get("confidence"),
                              source_span=quote)
            out.append(
                AdvisoryMeetingItem(
                    provenance=prov, state=state,
                    committee_type=_pick(meeting.get("committee_type") or doc.program,
                                         ADVISORY_COMMITTEE_TYPES),
                    committee_name=meeting.get("committee_name") or doc.program,
                    meeting_date=date,
                    meeting_year=_to_int(date) or doc.report_year,
                    item_type=_pick(it.get("item_type"), ADVISORY_ITEM_TYPES),
                    topic_category=_pick(it.get("topic_category"), ADVISORY_TOPICS),
                    speaker_role=_pick(it.get("speaker_role"), ADVISORY_SPEAKER_ROLES),
                    summary=it.get("summary"),
                    agency_response=it.get("agency_response"),
                    vote_result=_pick(it.get("vote_result"), VOTE_RESULTS),
                )
            )
        return out

    # ----- deterministic reference parser (canonical synthetic layout) -----
    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        """Synthetic fixture lines:
        MEETING | committee_name | committee_type | YYYY-MM-DD
        ITEM | page | item_type | topic | speaker_role | summary | response | vote | quote
        """
        meeting: dict[str, Any] = {}
        items: list[dict[str, Any]] = []
        for raw in text.splitlines():
            parts = [x.strip() for x in raw.split("|")]
            if parts[0] == "MEETING" and len(parts) >= 4:
                meeting = {"committee_name": parts[1], "committee_type": parts[2],
                           "meeting_date": parts[3]}
            elif parts[0] == "ITEM" and len(parts) >= 9:
                _, page, typ, topic, role, summ, resp, vote, quote = parts[:9]
                items.append({"page": int(page) if page.isdigit() else None,
                              "item_type": typ, "topic_category": topic, "speaker_role": role,
                              "summary": summ, "agency_response": resp or None,
                              "vote_result": vote or None, "quote": quote})
        base = {"source_document_id": doc.document_id, "source_url": doc.source_url,
                "method": ExtractionMethod.RULE, "extractor_version": self.version}
        return self.records_from_payload({"meeting": meeting, "items": items}, doc, base)
