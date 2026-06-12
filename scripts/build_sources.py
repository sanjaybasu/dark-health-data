#!/usr/bin/env python3
"""Turn a discovery-workflow output (rows of verified PDFs) into a live sources_<dataset>.yaml,
excluding URLs already in the published dist. Backs up the current (synthetic) sources first.

Reusable by the nightly automation so the per-round parse isn't hand-written each time.

Usage:  python scripts/build_sources.py <dataset> <workflow_output.json>
  -> writes registry/sources_<dataset>.yaml (new URLs only)
  -> prints "NEW=<n>" so the caller can detect diminishing returns
"""
from __future__ import annotations

import glob
import json
import shutil
import sys
import zipfile

import yaml

ABBR = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
        "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
        "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
        "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
        "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
        "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
        "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
        "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
        "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
        "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}


def main(dataset: str, out_path: str) -> int:
    data = json.load(open(out_path))
    rows = (data.get("result") or {}).get("rows") if isinstance(data, dict) else None
    if rows is None:
        rows = data.get("rows", data) if isinstance(data, dict) else data

    zips = sorted(glob.glob(f"dist/{dataset}-v*.zip"))
    have = set()
    if zips:
        zf = zipfile.ZipFile(zips[-1])
        rj = [n for n in zf.namelist() if n.endswith("records.jsonl")][0]
        have = {r["provenance"].get("source_url")
                for r in (json.loads(l) for l in zf.read(rj).decode().splitlines() if l.strip())}

    seen = set()
    src = []
    for h in rows:
        u = (h.get("url") or "").strip()
        if not u or u in seen or u in have or not h.get("verified_pdf"):
            continue
        seen.add(u)
        try:
            yr = int(h.get("year") or 2024)
        except Exception:
            yr = 2024
        st = ABBR.get(h.get("state"), h.get("state"))
        name = (h.get("hospital") or h.get("facility") or "")[:120]
        if dataset == "chna":
            src.append({"hospital": name, "state": st, "reports": [{"year": yr, "url": u}]})
        elif dataset == "nursing_home_2567":
            src.append({"state": st, "facility": name, "reports": [{"year": yr, "url": u}]})
        else:
            src.append({"state": st, "reports": [{"year": yr, "title": name, "url": u}]})

    bak = f"/tmp/sources_{dataset}.synthetic.bak"
    try:
        shutil.copy(f"registry/sources_{dataset}.yaml", bak)
    except Exception:
        pass
    with open(f"registry/sources_{dataset}.yaml", "w") as fh:
        fh.write(f"# Live {dataset} sources (automated round; new URLs only).\n")
        yaml.safe_dump({"sources": src}, fh, sort_keys=False, allow_unicode=True, width=300)
    print(f"NEW={len(src)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
