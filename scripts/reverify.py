#!/usr/bin/env python3
"""Re-run the verification layer on a processed dataset without re-extracting it.

Reads data/processed/<dataset>/records.jsonl and documents.csv, resets each row's QA
fields (record validators re-add their own flags), verifies against the cached document
text (data/cache/modal_text/<dataset>.jsonl, for grounding), and rewrites the tables and
generated docs. Use it after a change to a connector's constraints() or to verify/.

Usage: python scripts/reverify.py <dataset>
"""
from __future__ import annotations

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


def main(dataset: str) -> int:
    connector = get_connector(dataset)
    out_dir = settings.processed_dir / dataset
    classmap = _record_classmap(connector)
    records = []
    for line in (out_dir / "records.jsonl").open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        for k in ("qa_status", "qa_flags", "trust_score", "review_recommended"):
            r.pop(k, None)
        records.append(classmap[r["record_type"]].model_validate(r))
    docs = [SourceDocument.model_validate({k: (v if v != "" else None) for k, v in r.items()})
            for r in csv.DictReader((out_dir / "documents.csv").open(encoding="utf-8"))]
    texts = {}
    cache = settings.cache_dir / "modal_text" / f"{dataset}.jsonl"
    if cache.exists():
        for line in cache.open(encoding="utf-8"):
            c = json.loads(line)
            if c.get("ok") and c.get("document_id"):
                texts[c["document_id"]] = c["text"]
    report = verify_records(records, connector=connector, doc_texts=texts)
    curated = curate(records, docs, out_dir, write_parquet=True)
    meta = _dataset_meta(dataset)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)
    print(f"{dataset}: {report['n_records']} records; pass={report['qa_pass']} "
          f"warn={report['qa_warn']} fail={report['qa_fail']} mean_trust={report['mean_trust']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
