"""Tests for the Medicaid advisory committee and beneficiary council connector."""

from dark_health_data.connectors import get_connector
from dark_health_data.models import AdvisoryMeetingItem, SourceDocument
from dark_health_data.pipeline import run_dataset

DOC = SourceDocument(document_id="d", dataset_id="medicaid_advisory", jurisdiction="NC",
                     title="MCAC minutes 2024-06-07", program="medicaid advisory committee",
                     report_year=2024)
BASE = {"source_document_id": "d", "method": "llm"}


def test_medicaid_advisory_demo_run():
    summary = run_dataset("medicaid_advisory", extractor_name="rule", verbose=False,
                          write_parquet=False)
    assert summary["curation"]["tables"]["advisory_meeting_items"] == 2
    assert summary["validation"]["qa_fail"] == 0


def test_payload_maps_meeting_fields_and_quote():
    conn = get_connector("medicaid_advisory")
    payload = {"meeting": {"committee_name": "Medical Care Advisory Committee",
                           "committee_type": "Medicaid Advisory Committee",
                           "meeting_date": "2024-06-08"},
               "items": [{"item_type": "Recommendation", "topic_category": "transportation",
                          "speaker_role": "committee member",
                          "summary": "A member recommended NEMT ride tracking.",
                          "quote": "recommended that NEMT rides be tracked", "page": 3}]}
    (r,) = conn.records_from_payload(payload, DOC, BASE)
    assert isinstance(r, AdvisoryMeetingItem)
    # the posting's date (from discovery) wins over the date read from the text
    assert (r.meeting_date, r.meeting_year) == ("2024-06-07", 2024)
    assert (r.committee_type, r.item_type) == ("medicaid advisory committee", "recommendation")
    assert r.provenance.source_span == "recommended that NEMT rides be tracked"
    assert r.provenance.page_start == 3


def test_rules_flag_problems():
    conn = get_connector("medicaid_advisory")
    rules = {c.name: c for c in conn.constraints() if hasattr(c, "fn")}
    ok = conn.records_from_payload({"items": [{
        "item_type": "public comment", "topic_category": "dental and vision",
        "speaker_role": "member of the public", "summary": "A parent said dentists are scarce.",
        "quote": "few dentists accept Medicaid in the county"}]}, DOC, BASE)[0]
    for name in ("has_quote", "controlled_vocabulary", "no_identifying_text", "has_date"):
        assert rules[name].fn(ok)[0] is True, name
    named = ok.model_copy(update={"summary": "Ms. Jones said dentists are scarce."})
    assert rules["no_identifying_text"].fn(named)[0] is False
    odd = ok.model_copy(update={"topic_category": "weather"})
    assert rules["controlled_vocabulary"].fn(odd)[0] is False
    bad = AdvisoryMeetingItem(provenance=BASE, state="NC", meeting_date="June 7")
    assert any("YYYY-MM-DD" in f for f in bad.qa_flags)
