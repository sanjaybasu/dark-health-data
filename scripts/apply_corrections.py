#!/usr/bin/env python3
"""Apply checked corrections to a processed dataset and rebuild its tables.

A correction changes one field of one row to a value that a person or a check confirmed
against the source document. Each correction keeps its basis in the row's
``provenance.notes`` (and ``provenance.reviewer`` when a person made it), and the full list
is written to corrections.csv next to the tables. Rows are re-verified after the change.

Corrections file (CSV): key, field, old, new, reviewer, basis
  key       the dataset's row key (fair_hearing: decision_id)
  old       the value the row must hold now (the correction is refused otherwise)

Usage: python scripts/apply_corrections.py fair_hearing corrections.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402
from dark_health_data.connectors import get_connector  # noqa: E402
from dark_health_data.curate import curate  # noqa: E402
from dark_health_data.models import SourceDocument  # noqa: E402
from dark_health_data.pipeline import _dataset_meta, _record_classmap  # noqa: E402
from dark_health_data.publish import (  # noqa: E402
    write_croissant,
    write_data_dictionary,
    write_dataset_card,
)
from dark_health_data.verify import verify_records  # noqa: E402

KEY = {"fair_hearing": "decision_id"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=sorted(KEY))
    ap.add_argument("corrections")
    a = ap.parse_args()

    connector = get_connector(a.dataset)
    out_dir = settings.processed_dir / a.dataset
    rows = [json.loads(line) for line in (out_dir / "records.jsonl").open() if line.strip()]
    docs = [SourceDocument.model_validate({k: (v if v != "" else None) for k, v in d.items()})
            for d in csv.DictReader((out_dir / "documents.csv").open(encoding="utf-8"))]
    fixes = list(csv.DictReader(Path(a.corrections).open(encoding="utf-8")))
    by_key = {}
    for r in rows:
        by_key.setdefault(r[KEY[a.dataset]], []).append(r)

    for fx in fixes:
        hits = by_key.get(fx["key"], [])
        if len(hits) != 1:
            sys.exit(f"refused: {fx['key']} matches {len(hits)} rows")
        r = hits[0]
        if str(r.get(fx["field"])) != fx["old"]:
            sys.exit(f"refused: {fx['key']} {fx['field']} is {r.get(fx['field'])!r}, "
                     f"not {fx['old']!r}")
        r[fx["field"]] = fx["new"]
        note = f"{fx['field']} corrected from '{fx['old']}' to '{fx['new']}': {fx['basis']}"
        prov = r["provenance"]
        prov["notes"] = f"{prov['notes']}; {note}" if prov.get("notes") else note
        if fx.get("reviewer"):
            prov["reviewer"] = fx["reviewer"]

    classmap = _record_classmap(connector)
    records = []
    for r in rows:
        for k in ("qa_status", "qa_flags", "trust_score", "review_recommended"):
            r.pop(k, None)
        records.append(classmap[r["record_type"]].model_validate(r))
    records = connector.finalize(records)
    report = verify_records(records, connector=connector)
    curated = curate(records, docs, out_dir, write_parquet=True)
    meta = _dataset_meta(a.dataset)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)

    log = out_dir / "corrections.csv"
    old = list(csv.DictReader(log.open(encoding="utf-8"))) if log.exists() else []
    done = {(x["key"], x["field"]) for x in old}
    with log.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["key", "field", "old", "new", "reviewer", "basis"])
        w.writeheader()
        w.writerows(old + [fx for fx in fixes if (fx["key"], fx["field"]) not in done])
    print(f"applied {len(fixes)} correction(s); qa pass={report['qa_pass']} "
          f"warn={report['qa_warn']} fail={report['qa_fail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
