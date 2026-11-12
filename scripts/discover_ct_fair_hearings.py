#!/usr/bin/env python3
"""List Connecticut DSS Medicaid fair-hearing decisions into a live sources file.

Reads the three Medicaid categories of the public DSS list
(https://portal.ct.gov/dss/lists/administrative-hearings-decisions); SNAP is skipped.
portal.ct.gov/robots.txt allows these paths. One request every DELAY seconds.

Usage:  python scripts/discover_ct_fair_hearings.py [max_pages_per_category]
  -> writes registry/sources_fair_hearing_live.yaml.example (not auto-loaded; copy it
     into a run registry, as pilots/fair_hearing_2026-11/run_full.py does)
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import requests
import yaml

BASE = "https://portal.ct.gov/dss/lists/administrative-hearings-decisions"
CATEGORIES = {
    "ltss-eligibility": "LTSS Eligibility",
    "medical-services": "Medical Services",
    "other-medicaid-eligibility": "Other Medicaid Eligibility",
}
UA = "dark-health-data research crawler (public records; +https://github.com/sanjaybasu/dark-health-data)"
DELAY = 2.0
PDF = re.compile(r'href="([^"]+/fair-hearings/[^"]+\.pdf)(?:\?[^"]*)?"', re.I)
LAST = re.compile(r"Page \d+ of (\d+)")


def get(session: requests.Session, url: str) -> str:
    for attempt in range(3):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(DELAY * (attempt + 2))
    raise RuntimeError(f"failed: {url}")


def main(max_pages: int | None) -> None:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    out, seen = [], set()
    for slug, label in CATEGORIES.items():
        first = get(s, f"{BASE}/{slug}")
        m = LAST.search(first)
        n_pages = int(m.group(1)) if m else 1
        if max_pages:
            n_pages = min(n_pages, max_pages)
        decisions = []
        for page in range(1, n_pages + 1):
            html = first if page == 1 else get(s, f"{BASE}/{slug}?page={page}")
            for url in PDF.findall(html):
                url = url if url.startswith("http") else "https://portal.ct.gov" + url
                did = Path(url).stem.upper()
                if did in seen:
                    continue
                seen.add(did)
                yr = re.search(r"_(\d{4})_", did)
                decisions.append({"id": did, "year": int(yr.group(1)) if yr else None,
                                  "category": label, "url": url})
            print(f"{label}: page {page}/{n_pages}, {len(decisions)} decisions", flush=True)
            time.sleep(DELAY)
        out.append({"state": "CT", "publisher": "Connecticut Department of Social Services",
                    "category": label, "decisions": decisions})
    path = Path("registry/sources_fair_hearing_live.yaml.example")
    header = ("# Connecticut DSS Medicaid fair-hearing decisions (LTSS, medical services, other\n"
              "# Medicaid eligibility). Written by scripts/discover_ct_fair_hearings.py on "
              f"{time.strftime('%Y-%m-%d')}.\n")
    path.write_text(header + yaml.safe_dump({"sources": out}, sort_keys=False, width=200))
    print(f"wrote {path}: {sum(len(c['decisions']) for c in out)} decisions")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
