#!/usr/bin/env python3
"""Replace personal names with "[name]" in the free text of a processed dataset.

Meeting minutes name the people who speak ("was presented by Tracy Keen, Division of
Eligibility Administration"). The extraction instructions keep names out, but a name can
stay inside a verbatim quote, and a pattern cannot find untitled names. This step asks
Claude Haiku (Message Batches API) to list the personal names in each row's quote, summary,
and agency response, replaces each name with "[name]", and re-runs verification against
the source text with the same names replaced, so the grounding check stays meaningful.

It writes name_redactions.csv (row key, field, number of names replaced) next to the
tables. The names are not kept.

Usage: python scripts/redact_names.py medicaid_advisory [--model claude-haiku-5-5]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402
from dark_health_data.connectors import get_connector  # noqa: E402
from dark_health_data.curate import curate  # noqa: E402
from dark_health_data.models import SourceDocument  # noqa: E402
from dark_health_data.pipeline import _dataset_meta, _load_modal_text, _record_classmap  # noqa: E402
from dark_health_data.publish import (  # noqa: E402
    write_croissant,
    write_data_dictionary,
    write_dataset_card,
)
from dark_health_data.verify import verify_records  # noqa: E402

FIELDS = ("quote", "summary", "agency_response")
GROUP = 25  # rows per request
INSTRUCTIONS = (
    "You find personal names in short texts from public meeting minutes. For each item, "
    "list every name of a person (first name, last name, or full name) exactly as it is "
    "written in the text, including a name after a title (list 'Keen' for 'Ms. Keen'). Do "
    "not list organizations, agencies, health plans, programs, places, laws, job titles "
    "without a name, or words like 'Chair' or 'Director' alone. Return an empty list for "
    "an item with no personal name."
)
TOOL = {
    "name": "names",
    "description": "Personal names found in each item.",
    "input_schema": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"},
                           "names": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "names"]}}},
        "required": ["items"],
    },
}


def _texts(r: dict) -> dict[str, str]:
    return {"quote": r["provenance"].get("source_span") or "",
            "summary": r.get("summary") or "",
            "agency_response": r.get("agency_response") or ""}


def _sub(text: str, names: list[str]) -> tuple[str, int]:
    n = 0
    for name in sorted(names, key=len, reverse=True):
        body = r"\s+".join(re.escape(w) for w in name.split())
        pat = re.compile(rf"(?<![\w]){body}(?![\w])")
        text, k = pat.subn("[name]", text)
        n += k
    return text, n


# A name after a personal title is a name even when the word is also ordinary ("Ms. Grant").
_TITLED = re.compile(
    r"\b(?:(?:Mr|Mrs|Ms|Miss|Dr)\.?|Chairman|Chairwoman|Director|Commissioner|Secretary"
    r"|Senator|Representative|Delegate)\s+(?!(?:Medicaid|Finance|Executive|Chief|General|No"
    r"|Deputy|Medical|Assistant|of|for)\b)([A-Z][a-z'\u2019-]+)\b")
# An individual's age ("a 70-year-old female"); age rules such as "turning 65" are kept.
_AGE = re.compile(r"\b\d{1,3}(?=[- ]years?[- ]old\b)")


def _usable(name: str) -> bool:
    name = name.strip()
    return len(name) >= 3 and name[0].isupper() and name not in ("[name]",)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["medicaid_advisory"])
    ap.add_argument("--model", default="claude-haiku-5-5")
    a = ap.parse_args()

    connector = get_connector(a.dataset)
    out_dir = settings.processed_dir / a.dataset
    rows = [json.loads(line) for line in (out_dir / "records.jsonl").open() if line.strip()]
    docs = [SourceDocument.model_validate({k: (v if v != "" else None) for k, v in d.items()})
            for d in csv.DictReader((out_dir / "documents.csv").open(encoding="utf-8"))]

    # names found earlier are kept privately (never published) so a re-run needs no batch
    cache = settings.repo_root / "private" / f"{a.dataset}_names_found.json"
    if cache.exists():
        found = {(int(k.split("|")[0]), k.split("|")[1]): v
                 for k, v in json.loads(cache.read_text()).items()}
        print(f"using {len(found)} cached name lists from {cache}", flush=True)
    else:
        found = _find_names(rows, a.model)
        cache.write_text(json.dumps({f"{i}|{f}": v for (i, f), v in found.items()}))
    keep = _person_names(rows, found, a.model, settings.repo_root / "private"
                         / f"{a.dataset}_names_checked.json")
    found = {k: [n for n in v if n in keep] for k, v in found.items()}
    return _apply(a.dataset, connector, out_dir, rows, docs, found)


CHECK = (
    "Each item is a candidate string from public meeting minutes, with one sentence where "
    "it appears. Decide whether the candidate is the name of a person (first name, last "
    "name, or full name). Answer person=false for an organization, company, health plan, "
    "agency, program, acronym, place, law or bill, or any other non-person. Answer "
    "common_word=true when the candidate is also an ordinary English word or a common "
    "first word of a sentence (for example Grant, Bill, Will, May, Joy, Rich, Brand)."
)
CHECK_TOOL = {
    "name": "check",
    "description": "Classify each candidate.",
    "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": {
            "candidate": {"type": "string"}, "person": {"type": "boolean"},
            "common_word": {"type": "boolean"}},
        "required": ["candidate", "person", "common_word"]}}}, "required": ["items"]},
}


def _person_names(rows: list[dict], found: dict, model: str, cache: Path) -> set[str]:
    """Keep only candidates that a second pass judges to be a person's name and not also
    an ordinary word (those would replace ordinary text across a whole document)."""
    if cache.exists():
        verdict = json.loads(cache.read_text())
    else:
        import anthropic

        ctx: dict[str, str] = {}
        for (i, f), names in found.items():
            text = _texts(rows[i])[f]
            for n in names:
                if n not in ctx:
                    k = text.find(n)
                    ctx[n] = text[max(0, k - 80):k + len(n) + 80]
        client = anthropic.Anthropic()
        verdict = {}
        cands = sorted(ctx)
        for g in range(0, len(cands), 120):
            items = [{"candidate": c, "context": ctx[c]} for c in cands[g:g + 120]]
            resp = client.messages.create(
                model=model, max_tokens=8192, system=CHECK, tools=[CHECK_TOOL],
                tool_choice={"type": "tool", "name": "check"}, thinking={"type": "disabled"},
                messages=[{"role": "user", "content": json.dumps(items, ensure_ascii=False)}])
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use":
                    for it in (block.input or {}).get("items", []):
                        verdict[it["candidate"]] = [bool(it.get("person")),
                                                    bool(it.get("common_word"))]
        cache.write_text(json.dumps(verdict))
    keep = {n for n, (person, common) in verdict.items()
            if person and not common and not n.isupper() and not re.search(r"\d", n)}
    print(f"{len(keep)} of {len(verdict)} candidates kept as person names", flush=True)
    return keep


def _find_names(rows: list[dict], model: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    reqs = []
    for g in range(0, len(rows), GROUP):
        items = [{"id": f"{i}.{f}", "text": t} for i in range(g, min(g + GROUP, len(rows)))
                 for f, t in _texts(rows[i]).items() if t]
        reqs.append({"custom_id": f"g{g}", "params": {
            "model": model, "max_tokens": 4096, "system": INSTRUCTIONS, "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "names"}, "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": json.dumps(items, ensure_ascii=False)}]}})
    bid = client.messages.batches.create(requests=reqs).id
    print(f"submitted {bid} ({len(reqs)} requests for {len(rows)} rows)", flush=True)
    while client.messages.batches.retrieve(bid).processing_status != "ended":
        time.sleep(30)

    found: dict[tuple[int, str], list[str]] = {}
    failed = 0
    for res in client.messages.batches.results(bid):
        if res.result.type != "succeeded":
            failed += 1
            continue
        for block in res.result.message.content:
            if getattr(block, "type", "") == "tool_use":
                for it in (block.input or {}).get("items", []):
                    i, _, f = str(it.get("id", "")).partition(".")
                    if i.isdigit() and f in FIELDS:
                        found[(int(i), f)] = [x for x in it.get("names", []) if _usable(x)]
    print(f"{failed} request(s) failed", flush=True)
    return found


def _apply(dataset, connector, out_dir, rows, docs, found) -> int:

    # One name list per document (the union over its rows), applied to every row of the
    # document and to its source text, so quotes and source text are redacted alike.
    doc_names: dict[str, set] = {}
    for (i, _f), names in found.items():
        if names:
            doc_names.setdefault(rows[i]["provenance"]["source_document_id"], set()).update(names)
    # titled names, found by pattern in the rows, join the document's list as full strings
    for r in rows:
        for t in _texts(r).values():
            for m in _TITLED.finditer(t):
                doc_names.setdefault(r["provenance"]["source_document_id"], set()).add(m.group(0))
    log = []
    for r in rows:
        names = sorted(doc_names.get(r["provenance"]["source_document_id"], ()))
        for f, cur in _texts(r).items():
            new, n = _sub(cur, names)
            new, k = _AGE.subn("[age]", new)
            n += k
            if n:
                if f == "quote":
                    r["provenance"]["source_span"] = new
                else:
                    r[f] = new
                log.append((r["provenance"]["source_document_id"],
                            r["provenance"].get("page_start"), f, n))

    texts = _load_modal_text(dataset)
    doc_texts = {}
    for d in docs:
        t = (texts.get(d.source_url) or {}).get("text") or ""
        doc_texts[d.document_id] = _AGE.sub(
            "[age]", _sub(t, sorted(doc_names.get(d.document_id, ())))[0])

    classmap = _record_classmap(connector)
    records = []
    for r in rows:
        for k in ("qa_status", "qa_flags", "trust_score", "review_recommended"):
            r.pop(k, None)
        records.append(classmap[r["record_type"]].model_validate(r))
    records = connector.finalize(records)
    report = verify_records(records, connector=connector, doc_texts=doc_texts)
    curated = curate(records, docs, out_dir, write_parquet=True)
    meta = _dataset_meta(dataset)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)
    with (out_dir / "name_redactions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source_document_id", "page", "field", "names_replaced"])
        w.writerows(sorted(log, key=lambda x: (x[0], x[1] or 0, x[2])))
    print(f"replaced {sum(x[3] for x in log)} name(s) in {len(log)} field(s); "
          f"qa pass={report['qa_pass']} warn={report['qa_warn']} fail={report['qa_fail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
