#!/usr/bin/env python3
"""Publish a new dataset's Zenodo record, or a new version of a record this script made.

`publish_zenodo.py` versions the five original datasets. This script makes the record
for a new dataset (default) or, with --new-version <record_id>, adds a version to it:
the new version replaces the files and keeps the concept DOI. Metadata comes from a JSON
file (title, version, description_html, keywords); creators, license (CC0), and the link
to the code repository are fixed here.

Usage:  ZENODO_TOKEN=... python scripts/publish_zenodo_new.py dist/<id>-v<ver>.zip meta.json
            [--new-version <record_id>] [--draft]
        --draft stops before publishing (the draft can be checked on zenodo.org)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import requests

BASE = "https://zenodo.org/api"
CREATORS = [{"name": "Basu, Sanjay",
             "affiliation": "University of California, San Francisco; Waymark"}]
CODE = "https://github.com/sanjaybasu/dark-health-data"


def _deposit(P: dict, new_version: str | None) -> dict:
    if not new_version:
        r = requests.post(f"{BASE}/deposit/depositions", params=P, json={})
        r.raise_for_status()
        return r.json()
    r = requests.post(f"{BASE}/deposit/depositions/{new_version}/actions/newversion", params=P)
    r.raise_for_status()
    dep = requests.get(r.json()["links"]["latest_draft"], params=P)
    dep.raise_for_status()
    dep = dep.json()
    for f in dep.get("files", []):  # the new version starts with the old files: drop them
        requests.delete(f["links"]["self"], params=P).raise_for_status()
    return dep


def main(zip_path: str, meta_path: str, draft: bool, new_version: str | None) -> int:
    P = {"access_token": os.environ["ZENODO_TOKEN"]}
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    dep = _deposit(P, new_version)
    z = Path(zip_path)
    with z.open("rb") as fh:
        requests.put(f"{dep['links']['bucket']}/{z.name}", data=fh, params=P).raise_for_status()
    md = {
        "title": meta["title"], "upload_type": "dataset", "version": meta["version"],
        "description": meta["description_html"], "creators": CREATORS,
        "keywords": meta.get("keywords", []), "license": "cc-zero", "access_right": "open",
        "related_identifiers": [{"identifier": CODE, "relation": "isCompiledBy",
                                 "scheme": "url"}] + meta.get("related_identifiers", []),
    }
    if new_version:
        md["publication_date"] = datetime.date.today().isoformat()
    requests.put(f"{BASE}/deposit/depositions/{dep['id']}", params=P,
                 json={"metadata": md}).raise_for_status()
    if draft:
        print(f"DRAFT {dep['id']}: {dep['links']['html']}")
        return 0
    pub = requests.post(f"{BASE}/deposit/depositions/{dep['id']}/actions/publish", params=P)
    pub.raise_for_status()
    out = pub.json()
    print(f"PUBLISHED {meta['title']} v{meta['version']} | DOI {out.get('doi')} | "
          f"concept DOI {out.get('conceptdoi')} | record {out.get('record_id')}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("zip_path")
    ap.add_argument("meta_path")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--new-version", help="record id of the latest published version")
    a = ap.parse_args()
    sys.exit(main(a.zip_path, a.meta_path, a.draft, a.new_version))
