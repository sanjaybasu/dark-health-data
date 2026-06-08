"""Incremental-merge foundation: expansion must only ever ADD coverage.

A wave runs new sources and unions them with the published canonical dataset,
keyed by the stable content-hash document_id. These tests pin the guarantees the
adversarial review demanded:
- a re-run document replaces its prior records (no duplication), keyed on the
  content hash so URL-less docs and URL variants dedup correctly;
- every other prior document is retained (no regression);
- two merge waves chain through dist/ (wave 2 sees wave 1's additions) -- the
  blocker the review caught;
- a present-but-unreadable/empty/unknown canonical RAISES rather than silently
  republishing only the new wave.
"""
from __future__ import annotations

import csv
import zipfile

import pytest

from dark_health_data import pipeline
from dark_health_data.connectors import get_connector
from dark_health_data.connectors.mmrc import MMRCConnector
from dark_health_data.models import QAStatus, SourceDocument
from dark_health_data.release import package_dataset


def _mk(doc_id: str, url: str | None = None, year: int = 2024, state: str = "MS"):
    """An MMRC document (content-hash id ``doc_id``) + its 2 records."""
    doc = SourceDocument(document_id=doc_id, dataset_id="mmrc", jurisdiction=state,
                         report_year=year, source_url=url)
    prov = dict(source_document_id=doc_id, source_url=url, method="llm",
                model_name="claude-haiku-4-5", extractor_version="0.1.0")
    payload = {"findings": [{"population_group": "Overall", "pct_preventable": 80}],
               "recommendations": [{"recommendation": "Extend postpartum coverage"}]}
    return doc, MMRCConnector().records_from_payload(payload, doc, prov)


def _redirect_paths(tmp_path, monkeypatch):
    """Point settings at a throwaway repo so dist/ and data/processed/ live in tmp."""
    monkeypatch.setattr(pipeline.settings, "repo_root", tmp_path)
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path / "data")


def _write_processed(records, docs):
    out = pipeline.settings.processed_dir / "mmrc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "records.jsonl").write_text("\n".join(r.model_dump_json() for r in records),
                                       encoding="utf-8")
    fields = list(docs[0].model_dump(mode="json").keys())
    with (out / "documents.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for d in docs:
            writer.writerow(d.model_dump(mode="json"))


def test_merge_replaces_by_document_id_even_when_url_differs():
    docA, rA = _mk("idA", "http://x/A")
    docB, rB = _mk("idB", "http://x/B")
    # same content id, but the URL is a trailing-slash variant -> must still REPLACE
    docA2, rA2 = _mk("idA", "http://x/A/")
    docC, rC = _mk("idC", "http://x/C")

    merged_r, merged_d = pipeline._merge_records_docs(rA + rB, [docA, docB], rA2 + rC, [docA2, docC])

    assert sorted(d.document_id for d in merged_d) == ["idA", "idB", "idC"]  # A replaced, not duped
    a_recs = [r for r in merged_r if r.provenance.source_document_id == "idA"]
    assert a_recs == rA2  # the re-run records, not the originals
    assert len(merged_r) == len(rB) + len(rA2) + len(rC)


def test_merge_urlless_docs_dedup_by_id():
    docA, rA = _mk("idA", None)
    docA2, rA2 = _mk("idA", None)  # URL-less re-run must still replace, not duplicate
    merged_r, merged_d = pipeline._merge_records_docs(rA, [docA], rA2, [docA2])
    assert [d.document_id for d in merged_d] == ["idA"]
    assert merged_r == rA2


def test_merge_first_run_with_no_canonical_is_passthrough():
    docA, rA = _mk("idA", "http://x/A")
    merged_r, merged_d = pipeline._merge_records_docs([], [], rA, [docA])
    assert merged_r == rA and merged_d == [docA]


def test_aggregate_report_counts_qa_and_trust():
    _, recs = _mk("idA", "http://x/A")
    recs[0].qa_status, recs[0].trust_score = QAStatus.PASS, 0.9
    recs[1].qa_status, recs[1].trust_score, recs[1].review_recommended = QAStatus.WARN, 0.5, True

    rep = pipeline._aggregate_report(recs)
    assert rep["n_records"] == 2
    assert rep["qa_pass"] == 1 and rep["qa_warn"] == 1 and rep["qa_fail"] == 0
    assert rep["review_recommended"] == 1
    assert abs(rep["mean_trust"] - 0.7) < 1e-9


def test_two_wave_chain_accumulates_through_dist(tmp_path, monkeypatch):
    """The blocker: wave 2 must read wave 1's additions back from dist/, not a stale
    canonical. Exercises the real _load_canonical <- dist <- package_dataset chain."""
    _redirect_paths(tmp_path, monkeypatch)
    conn = get_connector("mmrc")

    # Wave 0: publish canonical = {A}
    docA, rA = _mk("idA", "http://x/A")
    _write_processed(rA, [docA])
    package_dataset("mmrc", tmp_path / "dist")

    # Wave 1: add B, republish via the same path the pipeline uses
    ex_r, ex_d = pipeline._load_canonical("mmrc", conn)
    assert {d.document_id for d in ex_d} == {"idA"}
    docB, rB = _mk("idB", "http://x/B")
    u_r, u_d = pipeline._merge_records_docs(ex_r, ex_d, rB, [docB])
    _write_processed(u_r, u_d)
    package_dataset("mmrc", tmp_path / "dist")

    # Wave 2: canonical MUST now be {A, B} (the regression was it reverting to {A})
    ex_r2, ex_d2 = pipeline._load_canonical("mmrc", conn)
    assert {d.document_id for d in ex_d2} == {"idA", "idB"}
    docC, rC = _mk("idC", "http://x/C")
    f_r, f_d = pipeline._merge_records_docs(ex_r2, ex_d2, rC, [docC])
    assert {d.document_id for d in f_d} == {"idA", "idB", "idC"}


def test_load_canonical_absent_returns_empty(tmp_path, monkeypatch):
    _redirect_paths(tmp_path, monkeypatch)
    (tmp_path / "dist").mkdir()
    assert pipeline._load_canonical("mmrc", get_connector("mmrc")) == ([], [])


def test_load_canonical_raises_on_empty_records(tmp_path, monkeypatch):
    """A present-but-empty canonical must RAISE, never return ([],[]) -- otherwise a
    merge would silently republish only the new wave."""
    _redirect_paths(tmp_path, monkeypatch)
    (tmp_path / "dist").mkdir()
    with zipfile.ZipFile(tmp_path / "dist" / "mmrc-v0.3.0.zip", "w") as zf:
        zf.writestr("mmrc/records.jsonl", "")
        zf.writestr("mmrc/documents.csv", "document_id,dataset_id\n")
    with pytest.raises(RuntimeError, match="zero records"):
        pipeline._load_canonical("mmrc", get_connector("mmrc"))


def test_load_canonical_raises_on_unknown_record_type(tmp_path, monkeypatch):
    _redirect_paths(tmp_path, monkeypatch)
    (tmp_path / "dist").mkdir()
    with zipfile.ZipFile(tmp_path / "dist" / "mmrc-v0.3.0.zip", "w") as zf:
        zf.writestr("mmrc/records.jsonl", '{"record_type": "not_a_real_type", "x": 1}')
        zf.writestr("mmrc/documents.csv", "document_id,dataset_id\n")
    with pytest.raises(RuntimeError, match="record_type"):
        pipeline._load_canonical("mmrc", get_connector("mmrc"))


def test_select_canonical_zip_is_semver_not_lexical(tmp_path, monkeypatch):
    _redirect_paths(tmp_path, monkeypatch)
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in ("mmrc-v0.9.0.zip", "mmrc-v0.10.0.zip", "mmrc-v0.2.0.zip"):
        (dist / name).write_bytes(b"x")
    assert pipeline._select_canonical_zip("mmrc").name == "mmrc-v0.10.0.zip"
