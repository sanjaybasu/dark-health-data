#!/usr/bin/env python3
"""List Medicaid MCO contract and amendment PDFs from state agency pages.

Step 1 of discovery: fetch each state's listing page (robots.txt checked; one request
every DELAY seconds; project user agent) and keep PDF links whose URL or link text names
a contract, agreement, or amendment. Writes data/cache/mco_contract_links.json for
review. Step 2 (choosing which documents to extract) is a separate, explicit selection.

Usage: python scripts/discover_mco_contracts.py [STATE ...]
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.robotparser
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

UA = "dark-health-data research crawler (public records; +https://github.com/sanjaybasu/dark-health-data)"
DELAY = 2.0
PAGES = {
    "WA": ["https://www.hca.wa.gov/billers-providers-partners/program-information-providers/model-managed-care-contracts"],
    "VA": ["https://www.dmas.virginia.gov/for-providers/cardinal-care-providers/cardinal-care-managed-care/"],
    "KY": ["https://www.chfs.ky.gov/agencies/dms/dhpo/Pages/mco-contracts.aspx"],
    "NC": ["https://medicaid.ncdhhs.gov/about-nc-medicaid/health-plans"],
    "LA": ["https://ldh.la.gov/medicaid/managed-care-contracts-public-comments"],
    "AZ": ["https://www.azahcccs.gov/Resources/OversightOfhealthPlans/SolicitationsAndContracts/contracts.html"],
    "PA": ["https://www.pa.gov/agencies/dhs/resources/medicaid/hc/hc-publications"],
}
KEEP = re.compile(r"contract|agreement|amend", re.I)
DROP = re.compile(r"report|template|naming|contact|tagline|form\b|instructions|checklist", re.I)
LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)


def allowed(url: str, cache: dict) -> bool:
    host = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if host not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(host + "/robots.txt", headers={"User-Agent": UA}, timeout=30)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        cache[host] = rp
    return cache[host].can_fetch(UA, url)


def main(states: list[str]) -> None:
    out_path = Path("data/cache/mco_contract_links.json")
    out = json.loads(out_path.read_text()) if out_path.exists() else {}
    robots: dict = {}
    s = requests.Session()
    s.headers["User-Agent"] = UA
    for st in states or list(PAGES):
        links = []
        for page in PAGES[st]:
            if not allowed(page, robots):
                print(f"{st}: robots.txt disallows {page}")
                continue
            try:
                html = s.get(page, timeout=60).text
            except requests.RequestException as exc:
                print(f"{st}: {type(exc).__name__}")
                continue
            for href, text in LINK.findall(html):
                text = re.sub(r"<[^>]+>|\s+", " ", unescape(text)).strip()
                url = urljoin(page, unescape(href))
                is_doc = re.search(r"\.pdf($|\?)|/download", url, re.I)
                if is_doc and KEEP.search(url + " " + text) and not DROP.search(url + " " + text):
                    links.append({"url": url, "text": text[:200]})
            time.sleep(DELAY)
        seen, uniq = set(), []
        for x in links:
            if x["url"] not in seen:
                seen.add(x["url"])
                uniq.append(x)
        out[st] = uniq
        print(f"{st}: {len(uniq)} contract/amendment links")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
