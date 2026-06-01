"""Curate typed records into tidy, analysis-ready tables.

Outputs (per run, into ``data/processed/<dataset_id>/``):

* ``<table>.csv`` -- one tidy table per record type, with provenance flattened
  into ``prov_*`` columns and ``qa_status`` / ``qa_flags`` retained.
* ``<table>.parquet`` -- same, if pyarrow is installed.
* ``documents.csv`` -- source-document metadata (the join key is ``document_id``).
* ``records.jsonl`` -- lossless nested records (every field + full provenance).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import RECORD_TABLE, ExtractionRecord, SourceDocument


def _flatten(record: ExtractionRecord) -> dict[str, Any]:
    d = record.model_dump(mode="json")
    prov = d.pop("provenance", {}) or {}
    flags = d.pop("qa_flags", []) or []
    d.pop("record_type", None)
    for k, v in prov.items():
        d[f"prov_{k}"] = v
    d["qa_flags"] = "; ".join(flags)
    return d


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    # union of keys, stable order: first-seen then sorted remainder
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> bool:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    table = pa.table({c: [r.get(c) for r in rows] for c in cols})
    pq.write_table(table, str(path))
    return True


def curate(
    records: list[ExtractionRecord],
    docs: list[SourceDocument],
    out_dir: Path,
    *,
    write_parquet: bool = True,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # group records into tables
    tables: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        table = RECORD_TABLE.get(r.record_type, r.record_type)
        tables.setdefault(table, []).append(_flatten(r))

    written: dict[str, int] = {}
    parquet_ok = False
    for table, rows in tables.items():
        _write_csv(out_dir / f"{table}.csv", rows)
        if write_parquet:
            parquet_ok = _write_parquet(out_dir / f"{table}.parquet", rows) or parquet_ok
        written[table] = len(rows)

    # documents table
    doc_rows = [d.model_dump(mode="json", exclude_none=True) for d in docs]
    if doc_rows:
        _write_csv(out_dir / "documents.csv", doc_rows)

    # lossless nested records
    with (out_dir / "records.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(r.model_dump_json(exclude_none=True) + "\n")

    return {"tables": written, "n_documents": len(docs), "parquet": parquet_ok}
