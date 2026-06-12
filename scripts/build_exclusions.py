#!/usr/bin/env python3
"""Write the per-state already-collected list the discovery crawlers read, from the
published dist. chna -> /tmp/chna_have.json ; nursing_home_2567 -> /tmp/nursing_have.json
(keys are full state names; values are collected hospital/facility names).

Usage:  python scripts/build_exclusions.py <dataset>
"""
from __future__ import annotations

import collections
import glob
import json
import sys
import zipfile

NAME = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
        "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
        "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
        "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
        "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
        "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
        "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
        "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
        "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
        "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
        "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"}


def main(dataset: str) -> int:
    key = "facility_name" if dataset == "nursing_home_2567" else "hospital_name"
    out = "/tmp/nursing_have.json" if dataset == "nursing_home_2567" else "/tmp/chna_have.json"
    zf = zipfile.ZipFile(sorted(glob.glob(f"dist/{dataset}-v*.zip"))[-1])
    rj = [n for n in zf.namelist() if n.endswith("records.jsonl")][0]
    by = collections.defaultdict(set)
    for r in (json.loads(l) for l in zf.read(rj).decode().splitlines() if l.strip()):
        st, name = r.get("state"), r.get(key)
        if st in NAME and name:
            by[NAME[st]].add(name[:80])
    json.dump({k: sorted(v) for k, v in by.items()}, open(out, "w"))
    print(f"{out}: {sum(len(v) for v in by.values())} names across {len(by)} states")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
