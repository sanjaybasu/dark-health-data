"""Regression: a chunk whose output hits max_tokens must not be dropped silently.

A truncated tool call comes back as an empty (or partial) input. Before this fix the
synchronous path skipped it and the batch path did `if not payload: continue`, so every
record in a dense multi-page chunk was lost with no log entry.
"""
from __future__ import annotations

from types import SimpleNamespace

from dark_health_data.connectors import get_connector
from dark_health_data.extract.llm import BatchLLMExtractor, LLMExtractor
from dark_health_data.models import SourceDocument

DOC = SourceDocument(document_id="t", dataset_id="eqr", source_url="http://x/r.pdf")


def _resp(stop, rows):
    block = SimpleNamespace(type="tool_use", name="emit_records",
                            input={"quality_measures": rows} if rows is not None else {})
    return SimpleNamespace(stop_reason=stop, content=[block])


class FakeClient:
    """Truncates any request holding more than one page; answers single pages."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        text = kw["messages"][0]["content"]
        pages = text.count("\n[[PAGE ")
        if pages > 1:
            return _resp("max_tokens", None)
        page = int(text.split("\n[[PAGE ")[1].split("]]")[0])
        return _resp("tool_use", [{"plan_name": "P", "measure_name": f"M{page}",
                                   "rate": 50.0, "reporting_year": "2023", "page": page}])


def test_sync_path_resends_truncated_chunk_page_by_page(monkeypatch):
    conn = get_connector("eqr")
    ex = LLMExtractor(model="m")
    fake = FakeClient()
    monkeypatch.setattr(ex, "_client", lambda: fake)
    text = "".join(f"\n[[PAGE {p}]]\n" + "x" * 50 for p in (1, 2, 3))
    records = ex.extract(text, DOC, conn)
    names = sorted(r.measure_name for r in records)
    assert names == ["M1", "M2", "M3"], names
    assert fake.calls == 4  # one truncated 3-page call, then 3 single-page calls


def test_batch_collect_reports_truncation_instead_of_skipping():
    conn = get_connector("eqr")
    msg = SimpleNamespace(stop_reason="max_tokens",
                          content=[SimpleNamespace(type="tool_use", name="emit_records", input={})])
    result = SimpleNamespace(custom_id="c0", result=SimpleNamespace(type="succeeded", message=msg))
    client = SimpleNamespace(messages=SimpleNamespace(
        batches=SimpleNamespace(results=lambda bid: [result])))
    records, dropped = BatchLLMExtractor(model="m").collect(client, "b", {"c0": DOC}, conn)
    assert records == []
    assert dropped == [("c0", "max_tokens")]
