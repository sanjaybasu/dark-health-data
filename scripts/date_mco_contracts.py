#!/usr/bin/env python3
"""Fill missing contract years from each document's own text.

For every contract in a sources file with `year: null`, send the first three pages of
the cached text (data/cache/modal_text/mco_contract.jsonl) to Claude and ask for the
effective date with a verbatim quote. A year is written only when the quote is found in
the text and contains that year; otherwise the year stays null. Each filled entry gets
`year_source: document text` and `year_quote`.

Usage: python scripts/date_mco_contracts.py <sources.yaml> [--model claude-haiku-5-5]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402

TOOL = {
    "name": "effective_date",
    "description": "Report when this contract or amendment takes effect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "effective_year": {"type": ["integer", "null"]},
            "quote": {"type": ["string", "null"],
                      "description": "verbatim text that states the effective date or contract period"},
        },
        "required": ["effective_year", "quote"],
    },
}
PROMPT = ("Below are the first pages of a state Medicaid managed care contract or contract "
          "amendment. Give the calendar year in which this document (or this amendment) "
          "takes effect, and copy the text that states it. If the pages do not state it, "
          "return nulls. Do not guess from other dates such as signature dates.\n\n")


def norm(t: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", (t or "").lower()).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources")
    ap.add_argument("--model", default="claude-haiku-5-5")
    a = ap.parse_args()
    import anthropic

    path = Path(a.sources)
    data = yaml.safe_load(path.read_text())
    cache = {}
    for line in (settings.cache_dir / "modal_text" / "mco_contract.jsonl").open():
        c = json.loads(line)
        if c.get("ok"):
            cache[c["url"]] = c["text"]
    client = anthropic.Anthropic()
    for src in data["sources"]:
        for c in src["contracts"]:
            if c.get("year") or c["url"] not in cache:
                continue
            head = "".join(re.split(r"(?=\[\[PAGE \d+\]\])", cache[c["url"]])[:4])[:12000]
            resp = client.messages.create(
                model=a.model, max_tokens=512, tools=[TOOL],
                tool_choice={"type": "tool", "name": "effective_date"},
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": PROMPT + head}],
            )
            out = next((b.input for b in resp.content if b.type == "tool_use"), {}) or {}
            yr, quote = out.get("effective_year"), out.get("quote") or ""
            ok = bool(yr) and str(yr) in quote and norm(quote) in norm(head)
            if ok:
                c["year"] = int(yr)
                c["year_source"] = "document text"
                c["year_quote"] = quote.strip()[:200]
            print(f"{src['state']} {c['title'][:60]}: {yr if ok else 'not found'}"
                  f"{'' if ok else f' (model said {yr})'}", flush=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=200))
    return 0


if __name__ == "__main__":
    sys.exit(main())
