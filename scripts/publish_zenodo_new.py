#!/usr/bin/env python3
"""Create and publish the FIRST Zenodo record for a new dataset.

`publish_zenodo.py` adds a version to an existing record; this script makes the
record. Metadata comes from a JSON file (title, version, description_html, keywords);
creators, license (CC0), and the link to the code repository are fixed here.

Usage:  ZENODO_TOKEN=... python scripts/publish_zenodo_new.py dist/<id>-v<ver>.zip meta.json
        add --draft to stop before publishing (the draft can be checked on zenodo.org)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

BASE = "https://zenodo.org/api"
CREATORS = [{"name": "Basu, Sanjay",
             "affiliation": "University of California, San Francisco; Waymark"}]
CODE = "https://github.com/sanjaybasu/dark-health-data"


def main(zip_path: str, meta_path: str, draft: bool) -> int:
    P = {"access_token": os.environ["ZENODO_TOKEN"]}
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    r = requests.post(f"{BASE}/deposit/depositions", params=P, json={})
    r.raise_for_status()
    dep = r.json()
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
    args = [a for a in sys.argv[1:] if a != "--draft"]
    sys.exit(main(args[0], args[1], "--draft" in sys.argv))
