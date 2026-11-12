#!/usr/bin/env python3
"""Build a human review workbook for a processed dataset, and score it when filled.

Two checks:
  rows   a random sample of rows, stratified by a column. The reviewer opens the cited
         page (the link goes to the PDF page) and marks each field 1 (right) or 0 (wrong).
         `correct` is 1 only when every field is right.
  pages  a random sample of pages that the extractor read. The reviewer lists what the
         page states that the dataset should hold but does not (missed rows). The rows
         already taken from that page are shown.

Rows are keyed by `review_id` = hash of document, page, and the row's own text, so the
key does not collide when rows share their identity columns.

Usage:
  python scripts/review_sheet.py build <dataset> --rows 150 --pages 20 --stratify state
  python scripts/review_sheet.py score <dataset> review/<dataset>_review.xlsx
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402
from dark_health_data.connectors import get_connector  # noqa: E402
from dark_health_data.evaluation import primary_table, wilson_ci  # noqa: E402

# Fields the reviewer grades, per dataset (each gets a 1/0 column).
GRADE = {
    "mco_contract": ["in_scope", "provision_category", "requirement", "target_population",
                     "numeric_value_unit", "obligation"],
    "fair_hearing": ["outcome", "action_type", "issue_category", "service_at_issue",
                     "outcome_reason", "remedy_ordered", "amount_at_issue"],
}
GUIDE = {
    "mco_contract": [
        "Open each row's page link (the PDF opens at the cited page; the provision can run onto the next page).",
        "Put 1 (right) or 0 (wrong) in each ok_ column. Leave a cell blank only if you cannot tell.",
        "ok_in_scope: the provision is a contract requirement in one of the 10 categories "
        "(care coordination and care management; health risk assessment and screening; "
        "community health workers; health-related social needs and in lieu of services; "
        "behavioral health integration; value-based payment; quality incentives and withholds; "
        "network adequacy and access; prior authorization and utilization management; "
        "member outreach and engagement). Mark 0 for a definition, a table-of-contents line, "
        "background text, a duty to submit or report something to the state (a policy, plan, report, data, or "
        "corrective action plan), a general compliance sanction, or an out-of-scope topic (claims, credentialing, "
        "provider contracting and notices, encounter data, financial terms, rates, call-center or "
        "member-services hours, grievances, marketing, covered medical benefits other than behavioral health or "
        "social needs).",
        "ok_provision_category: the category is the best of the 10. Any mental health or substance use service, "
        "crisis service, parity, or physical-behavioral coordination is behavioral health integration; provider "
        "counts, capacity, PCP assignment, office hours, and on-call availability are network adequacy and access. "
        "If ok_in_scope is 0, put 0.",
        "ok_requirement: the restatement says what the contract says, with nothing added or lost that changes the meaning.",
        "ok_target_population: right, or correctly blank when the text names no group.",
        "ok_numeric_value_unit: the number and unit match the text (for example 30 / days), or both are correctly "
        "blank. A frequency word with no printed number (annually, monthly) should be blank.",
        "ok_obligation: must (required), may (allowed), incentive (paid for doing it), penalty (charged for not doing it).",
        "correct: 1 only if every ok_ column is 1. notes: anything systematic you see.",
        "pages sheet: open each page; in n_missed put how many in-scope provisions the page states that are "
        "not in rows_already_from_this_page (0 if none), and list them briefly.",
    ],
    "fair_hearing": [
        "Open each row's page link (the decision PDF; the page is the DECISION or ORDER section).",
        "Put 1 (right) or 0 (wrong) in each ok_ column. Leave a cell blank only if you cannot tell.",
        "ok_outcome: a stated GRANTED/SUSTAINED, DENIED, REMANDED, DISMISSED, or WITHDRAWN is "
        "used as written; a moot issue is dismissed; otherwise an order to reverse, reopen, "
        "redetermine, recompute, or restore is granted. 'granted in part' when only some issues win.",
        "ok_action_type, ok_issue_category: the best label from the controlled list.",
        "ok_service_at_issue, ok_outcome_reason, ok_remedy_ordered: faithful to the decision; blank is right only when the decision has none.",
        "ok_amount_at_issue: the dollar amount the decision prints as contested, or correctly blank.",
        "Privacy: mark 0 in a field (and write 'privacy' in notes) if it states a name, age, birth date, "
        "address, or ID number of the appellant or family. Clinical facts the decision states are allowed.",
        "correct: 1 only if every ok_ column is 1.",
    ],
}
SHOW = {
    "mco_contract": ["state", "document_title", "contract_year", "section",
                     "provision_category", "requirement", "target_population",
                     "numeric_value", "numeric_unit", "obligation"],
    "fair_hearing": ["decision_id", "decision_year", "decision_category", "program",
                     "respondent", "action_type", "issue_category", "service_at_issue",
                     "outcome", "outcome_reason", "remedy_ordered", "amount_at_issue"],
}


def review_id(r: dict) -> str:
    raw = "|".join(str(r.get(k, "")) for k in
                   ("prov_source_document_id", "prov_page_start", "requirement",
                    "outcome_reason", "prov_source_span", "decision_id"))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def page_link(r: dict) -> str:
    url, pg = r.get("prov_source_url", ""), r.get("prov_page_start", "")
    return f"{url}#page={pg}" if url and pg else url


def load(dataset: str) -> list[dict]:
    conn = get_connector(dataset)
    path = settings.processed_dir / dataset / f"{primary_table(conn)}.csv"
    return list(csv.DictReader(path.open(encoding="utf-8")))


def build(a) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    rows = load(a.dataset)
    rng = random.Random(a.seed)
    strata = defaultdict(list)
    for r in rows:
        strata[r.get(a.stratify, "")].append(r)
    # Equal-ish allocation with a floor, so small states are not left with 1-2 rows.
    per = max(1, a.rows // len(strata))
    pick = []
    for g in strata.values():
        pick += rng.sample(g, min(len(g), per))
    rest = [r for r in rows if r not in pick]
    pick += rng.sample(rest, max(0, a.rows - len(pick)))
    rng.shuffle(pick)

    wb = Workbook()
    ws = wb.active
    ws.title = "rows"
    grade = GRADE[a.dataset]
    head = (["review_id", "page_link", "page"] + SHOW[a.dataset] + ["quote (prov_source_span)"]
            + [f"ok_{g}" for g in grade] + ["correct", "notes"])
    ws.append(head)
    for r in pick:
        ws.append([review_id(r), page_link(r), r.get("prov_page_start")]
                  + [r.get(c, "") for c in SHOW[a.dataset]] + [r.get("prov_source_span", "")]
                  + [""] * len(grade) + ["", ""])
        c = ws.cell(row=ws.max_row, column=2)
        c.hyperlink, c.value, c.font = c.value, "open page", Font(color="0563C1", underline="single")
    for col in ws.iter_cols(min_row=1, max_row=1):
        col[0].font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"

    # Pages sheet: pages the extractor read, sampled from the text cache.
    cache = settings.cache_dir / "modal_text" / f"{a.dataset}.jsonl"
    docs = {}
    for line in cache.open(encoding="utf-8"):
        c = json.loads(line)
        if c.get("ok"):
            docs[c["document_id"]] = (c["url"], len(re.findall(r"\[\[PAGE \d+\]\]", c["text"])))
    by_page = defaultdict(list)
    for r in rows:
        by_page[(r["prov_source_document_id"], r.get("prov_page_start"))].append(r)
    allpages = [(d, p) for d, (_, n) in docs.items() for p in range(1, n + 1)]
    gs = wb.create_sheet("instructions", 0)
    gs.column_dimensions["A"].width = 140
    for i, t in enumerate(GUIDE[a.dataset], 1):
        gs.append([f"{i}. {t}"])
        gs.cell(row=i, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.cell(row=1, column=len(head) + 1, value=f"stratum ({a.stratify})")
    for i, r in enumerate(pick, 2):
        ws.cell(row=i, column=len(head) + 1, value=r.get(a.stratify, ""))
    ps = wb.create_sheet("pages")
    ps.append(["page_id", "page_link", "document", "page", "rows_already_from_this_page",
               "n_missed", "missed_provisions (one per line: what the page states)", "notes"])
    dcsv = settings.processed_dir / a.dataset / "documents.csv"
    titles = {d["document_id"]: d.get("title", "")
              for d in csv.DictReader(dcsv.open(encoding="utf-8"))}
    for d, p in rng.sample(allpages, min(a.pages, len(allpages))):
        have = by_page.get((d, str(p)), [])
        ps.append([f"{d[:8]}-p{p}", f"{docs[d][0]}#page={p}", titles.get(d, d), p,
                   "\n".join("- " + (h.get("requirement") or h.get("outcome_reason") or "")[:200]
                             for h in have) or "(none)", "", "", ""])
        c = ps.cell(row=ps.max_row, column=2)
        c.hyperlink, c.value, c.font = c.value, "open page", Font(color="0563C1", underline="single")
    for row in ps.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    out = Path(a.out or f"review/{a.dataset}_review.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"{out}: {len(pick)} rows ({a.stratify}: "
          f"{dict(sorted((k, sum(1 for r in pick if r.get(a.stratify, '') == k)) for k in strata))}),"
          f" {ps.max_row - 1} pages")


def _bit(v):
    s = str(v).strip().lower() if v is not None else ""
    return True if s in ("1", "y", "yes", "true") else False if s in ("0", "n", "no", "false") else None


def score(a) -> None:
    from openpyxl import load_workbook

    wb = load_workbook(a.workbook)
    ws = wb["rows"]
    vals = list(ws.iter_rows(values_only=True))
    head, body = list(vals[0]), vals[1:]
    out = {"workbook": str(a.workbook), "fields": {}}
    for i, h in enumerate(head):
        if h and (h.startswith("ok_") or h == "correct"):
            bits = [b for b in (_bit(r[i]) for r in body) if b is not None]
            k = sum(bits)
            out["fields"][h] = {"right": k, "graded": len(bits),
                                "share": round(k / len(bits), 3) if bits else None,
                                "ci95": [round(x, 3) for x in wilson_ci(k, len(bits))] if bits else None}
    # Rows were sampled equally per stratum: weight each stratum by its share of the data.
    si = len(head) - 1 if str(head[-1]).startswith("stratum") else None
    if si is not None and "correct" in head:
        key = str(head[-1])[len("stratum ("):-1]
        pop = defaultdict(int)
        for r in load(a.dataset):
            pop[r.get(key, "")] += 1
        ci = head.index("correct")
        per = defaultdict(list)
        for r in body:
            b = _bit(r[ci])
            if b is not None:
                per[r[si] or ""].append(b)
        tot = sum(pop[k] for k in per)
        out["correct_by_stratum"] = {k: f"{sum(v)}/{len(v)}" for k, v in sorted(per.items())}
        out["correct_weighted"] = round(sum(pop[k] / tot * sum(v) / len(v)
                                            for k, v in per.items()), 3) if tot else None
    ps = wb["pages"]
    pv = list(ps.iter_rows(values_only=True))[1:]
    pv = [r for r in pv if str(r[5] if r[5] is not None else "").strip()]
    missed = [int(float(r[5])) for r in pv]
    found = sum(len([x for x in str(r[4]).split("\n") if x.startswith("- ")]) for r in pv)
    out["pages"] = {"reviewed": len(missed), "rows_on_pages": found, "missed": sum(missed),
                    "recall_estimate": round(found / (found + sum(missed)), 3)
                    if found + sum(missed) else None}
    dest = settings.processed_dir / a.dataset / "human_review.json"
    dest.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("dataset")
    b.add_argument("--rows", type=int, default=150)
    b.add_argument("--pages", type=int, default=20)
    b.add_argument("--stratify", default="state")
    b.add_argument("--seed", type=int, default=20261112)
    b.add_argument("--out")
    s = sub.add_parser("score")
    s.add_argument("dataset")
    s.add_argument("workbook")
    a = ap.parse_args()
    build(a) if a.cmd == "build" else score(a)


if __name__ == "__main__":
    main()
