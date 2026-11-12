"""Tests for the Medicaid fair-hearing connector."""

from dark_health_data.connectors import get_connector
from dark_health_data.models import FairHearingDecision, SourceDocument
from dark_health_data.pipeline import run_dataset

DOC = SourceDocument(document_id="d", dataset_id="fair_hearing", jurisdiction="CT",
                     title="MSRV_2018_1", program="Medical Services", report_year=2018)


def test_fair_hearing_demo_run():
    summary = run_dataset("fair_hearing", extractor_name="rule", verbose=False, write_parquet=False)
    assert summary["curation"]["tables"]["fair_hearing_decisions"] == 1
    assert summary["validation"]["qa_fail"] == 0


def test_payload_maps_to_one_case_level_row():
    conn = get_connector("fair_hearing")
    payload = {"decisions": [{
        "decision_year": "2018", "program": "HUSKY A", "outcome": "Granted",
        "legal_authorities": ["42 CFR 441.500", "Conn. Gen. Stat. 17b-2"],
        "amount_at_issue": "$20,790.86", "page": 6,
    }]}
    recs = conn.records_from_payload(payload, DOC, {"source_document_id": "d", "method": "llm"})
    assert len(recs) == 1
    r = recs[0]
    assert isinstance(r, FairHearingDecision)
    assert (r.state, r.decision_id, r.decision_category) == ("CT", "MSRV_2018_1", "Medical Services")
    assert r.outcome == "granted" and r.decision_year == 2018
    assert r.amount_at_issue == 20790.86
    assert r.legal_authorities == "42 CFR 441.500; Conn. Gen. Stat. 17b-2"


def test_identifying_text_is_flagged():
    conn = get_connector("fair_hearing")
    rule = next(c for c in conn.constraints() if c.name == "no_identifying_text")
    clean = FairHearingDecision(provenance={"source_document_id": "d", "method": "llm"},
                                state="CT", decision_id="x",
                                issue_statement="Whether the Department correctly denied the application")
    leaky = clean.model_copy(update={"issue_statement": "Whether Ms. Smith, a 67-year-old, is eligible"})
    assert rule.fn(clean)[0] is True
    assert rule.fn(leaky)[0] is False
    dob = clean.model_copy(update={"outcome_reason": "The Appellant, born 3/4/1950, was eligible"})
    assert rule.fn(dob)[0] is False
    # Clinical facts the public, redacted decision states are kept as written.
    scored = clean.model_copy(update={"outcome_reason": "The child's score of 22 was below 26"})
    assert rule.fn(scored)[0] is True
    care = clean.model_copy(update={"outcome_reason": "The Appellant needs help with four ADLs"})
    assert rule.fn(care)[0] is True


def test_labels_map_to_controlled_lists():
    conn = get_connector("fair_hearing")
    payload = {"decisions": [{"action_type": "Discontinuance", "issue_category": "eligibility - income",
                              "outcome": "denied"}]}
    r = conn.records_from_payload(payload, DOC, {"source_document_id": "d", "method": "llm"})[0]
    assert (r.action_type, r.issue_category) == ("termination", "eligibility - income")
    vocab = next(c for c in conn.constraints() if c.name == "controlled_vocabulary")
    assert vocab.fn(r)[0] is True
    assert vocab.fn(r.model_copy(update={"action_type": "spend-down"}))[0] is False


def test_whole_decision_goes_in_one_request():
    from dark_health_data.extract.llm import _chunk, _chunk_size
    conn = get_connector("fair_hearing")
    text = "".join(f"\n[[PAGE {p}]]\n" + "x" * 2500 for p in range(1, 9))  # 8 pages, ~20k chars
    assert len(_chunk(text, _chunk_size(conn))) == 1
    assert len(_chunk(text, _chunk_size(get_connector("eqr")))) > 1
