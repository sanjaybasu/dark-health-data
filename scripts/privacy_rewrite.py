#!/usr/bin/env python3
"""Remove personal health facts from free-text fields of a processed fair-hearing dataset.

The extraction prompt forbids stating a health fact about the appellant, but the model
still writes some ("the Appellant does not have a diagnosis of type 2 diabetes"; "the
child's malocclusion score of 22 points"). This step finds every free-text field that
the connector's `_IDENTIFYING` pattern matches, asks Claude to restate only that field
in policy terms (the coverage rule stays; the person's own clinical fact goes), checks
the rewrite with the same pattern, and rebuilds the tables. A rewrite that still
matches is not used: the field keeps its text and the `no_identifying_text` flag.

It writes privacy_rewrites.csv (decision_id, field, status) next to the tables. The
original text is not kept there.

Usage: python scripts/privacy_rewrite.py fair_hearing [--model claude-haiku-5-5]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402
from dark_health_data.connectors import get_connector  # noqa: E402
from dark_health_data.connectors.fair_hearing import _FREE_TEXT, _IDENTIFYING  # noqa: E402
from dark_health_data.curate import curate  # noqa: E402
from dark_health_data.models import SourceDocument  # noqa: E402
from dark_health_data.pipeline import _dataset_meta, _record_classmap  # noqa: E402
from dark_health_data.publish import (  # noqa: E402
    write_croissant,
    write_data_dictionary,
    write_dataset_card,
)
from dark_health_data.verify import verify_records  # noqa: E402

INSTRUCTIONS = (
    "You edit one field of a structured record about a public Medicaid fair-hearing "
    "decision. Rewrite the text so that it states no health fact about the appellant or "
    "a family member: no diagnosis they have or lack, no condition, score, test result, "
    "or assessment result. Keep the coverage rule in general terms, and keep every other "
    "fact, the meaning, and the length about the same.\n"
    "Examples:\n"
    "- 'because the Appellant does not have a diagnosis of Type 2 diabetes' -> 'because "
    "the drug is covered only for type 2 diabetes and the Appellant did not meet that "
    "coverage criterion'\n"
    "- 'the child's malocclusion score of 22 points was below the 26-point threshold' -> "
    "'the child did not meet the 26-point malocclusion threshold'\n"
    "- 'the Appellant did not establish a primary diagnosis of autism before age 22' -> "
    "'the Appellant did not meet the waiver's diagnostic criteria'\n"
    "Return only the rewritten text."
)
TOOL = {
    "name": "rewrite",
    "description": "Return the rewritten field text.",
    "input_schema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]},
}


def _call(client, model: str, content: str) -> str | None:
    for attempt in range(4):
        try:
            resp = client.messages.create(
                model=model, max_tokens=1024, system=INSTRUCTIONS, tools=[TOOL],
                tool_choice={"type": "tool", "name": "rewrite"},
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:  # noqa: BLE001 - back off, retry, then give up
            print(f"  api error ({type(exc).__name__}); retrying", flush=True)
            time.sleep(2 ** attempt * 5)
            continue
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return (block.input or {}).get("text")
    return None


def rewrite(client, model: str, text: str) -> str | None:
    """Rewrite; while the result still matches, re-send it naming the phrase (3 rounds)."""
    new = _call(client, model, text)
    for _ in range(2):
        m = _IDENTIFYING.search(new or "")
        if not (new and m):
            break
        new = _call(client, model, f"{new}\n\n(This text still contains '{m.group(0)}', "
                                   "which states a health fact about the person. Remove it.)")
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["fair_hearing"])
    ap.add_argument("--model", default="claude-haiku-5-5")
    a = ap.parse_args()

    import anthropic

    connector = get_connector(a.dataset)
    out_dir = settings.processed_dir / a.dataset
    classmap = _record_classmap(connector)
    rows = [json.loads(line) for line in (out_dir / "records.jsonl").open() if line.strip()]
    docs = [SourceDocument.model_validate({k: (v if v != "" else None) for k, v in r.items()})
            for r in csv.DictReader((out_dir / "documents.csv").open(encoding="utf-8"))]

    jobs = [(i, f) for i, r in enumerate(rows) for f in _FREE_TEXT
            if r.get(f) and _IDENTIFYING.search(r[f])]
    print(f"{len(jobs)} field(s) in {len({i for i, _ in jobs})} row(s) to rewrite", flush=True)
    client = anthropic.Anthropic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        outs = list(pool.map(lambda j: rewrite(client, a.model, rows[j[0]][j[1]]), jobs))

    log = []
    for (i, f), new in zip(jobs, outs):
        if new and not _IDENTIFYING.search(new):
            rows[i][f] = new.strip()
            log.append((rows[i]["decision_id"], f, "rewritten"))
        else:
            log.append((rows[i]["decision_id"], f, "unchanged: " + (
                "rewrite still matched" if new else "no rewrite returned")))

    # rehydrate with fresh QA (model validators re-add their own flags), then re-verify
    records = []
    for r in rows:
        for k in ("qa_status", "qa_flags", "trust_score", "review_recommended"):
            r.pop(k, None)
        records.append(classmap[r["record_type"]].model_validate(r))
    report = verify_records(records, connector=connector)
    curated = curate(records, docs, out_dir, write_parquet=True)
    meta = _dataset_meta(a.dataset)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)
    # merge with an earlier run's log, so a re-run (which only sees still-flagged
    # fields) keeps the record of fields already rewritten
    logp = out_dir / "privacy_rewrites.csv"
    merged = {}
    if logp.exists():
        merged = {(e["decision_id"], e["field"]): e["status"]
                  for e in csv.DictReader(logp.open(encoding="utf-8"))}
    merged.update({(d, f): s for d, f, s in log})
    with logp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["decision_id", "field", "status"])
        w.writerows((d, f, s) for (d, f), s in sorted(merged.items()))
    n_ok = sum(s == "rewritten" for *_, s in log)
    print(f"rewritten {n_ok}/{len(log)} field(s); qa pass={report['qa_pass']} "
          f"warn={report['qa_warn']} fail={report['qa_fail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
