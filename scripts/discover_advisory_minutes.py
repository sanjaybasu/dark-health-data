#!/usr/bin/env python3
"""List posted Medicaid advisory committee and beneficiary council minutes (PDF).

Reads a config of index pages (one or more per state and committee), keeps links that
point to a PDF and look like minutes, reads the meeting date from the link text or file
name, and writes a live sources file. Each host's robots.txt is checked before any
request; a disallowed page is skipped and reported. One request every DELAY seconds.

Config (YAML):
  pages:
    - state: NC
      committee: medicaid advisory committee      # one of ADVISORY_COMMITTEE_TYPES
      publisher: NC Department of Health and Human Services
      url: https://...index page...
      keywords: [minutes]                          # optional; default [minutes]
      exclude: [agenda, presentation]              # optional; link text/href to drop
      split_bac: true                               # optional; BAC links on a MAC page
      match: name                                   # optional; keywords in link text/file name only

Usage:  python scripts/discover_advisory_minutes.py <config.yaml> [out.yaml]
  -> default out: registry/sources_medicaid_advisory_live.yaml.example
"""
from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dark_health_data.crawl import find_report_links  # noqa: E402

UA = "dark-health-data research crawler (public records; +https://github.com/sanjaybasu/dark-health-data)"
DELAY = 2.0
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_PATTERNS = [
    (re.compile(r"(20\d{2}|19\d{2})[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd"),
    (re.compile(_MON + r"[\s_-]*(\d{1,2})(?:st|nd|rd|th)?,?[\s_-]*(20\d{2}|19\d{2})", re.I), "mdy_name"),
    (re.compile(r"(\d{1,2})[\s_-]*" + _MON + r"[\s_-]*(20\d{2}|19\d{2})", re.I), "dmy_name"),
    (re.compile(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](20\d{2}|\d{2})(?!\d)"), "mdy"),
    (re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)"), "mdy"),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)"), "mdy"),
]


def parse_date(text: str) -> str | None:
    """First plausible YYYY-MM-DD in a link text or file name, else None."""
    for pat, kind in _PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            try:
                if kind == "ymd":
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                elif kind == "mdy_name":
                    mo, d, y = MONTHS[g[0][:3].lower()], int(g[1]), int(g[2])
                elif kind == "dmy_name":
                    d, mo, y = int(g[0]), MONTHS[g[1][:3].lower()], int(g[2])
                else:
                    mo, d, y = int(g[0]), int(g[1]), int(g[2])
                    y = y + 2000 if y < 100 else y
            except (KeyError, ValueError):
                continue
            if 1966 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


_robots: dict[str, robotparser.RobotFileParser] = {}


def allowed(url: str) -> bool:
    host = "{0.scheme}://{0.netloc}".format(urlparse(url))
    if host not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(f"{host}/robots.txt", headers={"User-Agent": UA}, timeout=30)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        _robots[host] = rp
    return _robots[host].can_fetch(UA, url)


def main(config: str, out: str) -> int:
    pages = yaml.safe_load(Path(config).read_text())["pages"]
    by_key: dict[tuple, dict] = {}
    meetings: dict[tuple, list] = defaultdict(list)
    seen: set[str] = set()
    for p in pages:
        if not allowed(p["url"]):
            print(f"[skip] robots.txt disallows {p['url']}")
            continue
        try:
            r = requests.get(p["url"], headers={"User-Agent": UA}, timeout=60)
        except requests.RequestException as exc:
            print(f"[skip] {p['url']}: {type(exc).__name__}")
            continue
        time.sleep(DELAY)
        if r.status_code != 200:
            print(f"[skip] {p['url']}: HTTP {r.status_code}")
            continue
        links = find_report_links(r.text, p["url"], keywords=p.get("keywords", ["minutes"]),
                                  prefer_recent=False)
        drop = [x.lower() for x in p.get("exclude", [])]
        key = (p["state"], p["committee"])
        by_key[key] = {"state": p["state"], "committee": p["committee"],
                       "publisher": p.get("publisher")}
        n = 0
        for lk in links:
            hay = f"{lk['url']} {lk['title'] or ''}".lower()
            path = urlparse(lk["url"]).path
            if lk["url"] in seen or any(x in hay for x in drop):
                continue
            if p.get("match") == "name":  # keyword must be in the link text or file name
                name = f"{lk['title'] or ''} {Path(path).name}".lower()
                if not any(k in name for k in p.get("keywords", ["minutes"])):
                    continue
            if not allowed(lk["url"]):
                continue
            committee = p["committee"]
            if p.get("split_bac") and re.search(r"beneficiary|\bbac\b|[_-]bac[_.-]", hay):
                committee = "beneficiary advisory council"
            date = parse_date(f"{lk['title'] or ''} {Path(path).name}") or parse_date(path)
            seen.add(lk["url"])
            title = f"{p['state']} {committee} minutes {date or 'date unknown'}"
            meetings[key].append({"title": title, "url": lk["url"],
                                  "year": int(date[:4]) if date else lk["year"],
                                  "committee": committee})
            n += 1
        print(f"[ok]   {p['state']} {p['committee']}: {n} minutes from {p['url']}")
    sources = [{**by_key[k], "meetings": sorted(v, key=lambda m: m["title"])}
               for k, v in meetings.items() if v]
    head = ("# Live sources for medicaid_advisory, written by "
            "scripts/discover_advisory_minutes.py.\n# Not auto-loaded: copy into a run "
            "registry.\n")
    Path(out).write_text(head + yaml.safe_dump({"sources": sources}, sort_keys=False,
                                               allow_unicode=True))
    total = sum(len(v) for v in meetings.values())
    undated = sum(1 for v in meetings.values() for m in v if "date unknown" in m["title"])
    print(f"{total} minutes documents in {len(sources)} state-committee lists "
          f"({undated} without a date) -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else
                  "registry/sources_medicaid_advisory_live.yaml.example"))
