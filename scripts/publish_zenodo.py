#!/usr/bin/env python3
"""Publish the current dist/<dataset>-v*.zip as a NEW version of its Zenodo record.

Reusable + idempotent-friendly: resolves the dataset's latest published version via its
concept record, creates a new version, replaces the file, bumps the patch version, writes a
counts-derived description, and publishes. Removes the hardcoded-DOI fragility from the
per-round flow so the nightly automation can publish without hand-edits.

Usage:  ZENODO_TOKEN=... python scripts/publish_zenodo.py <dataset>
"""
from __future__ import annotations

import collections
import glob
import json
import os
import sys
import zipfile

import requests

BASE = "https://zenodo.org/api"

# dataset -> Zenodo CONCEPT recid (resolves to the latest version)
CONCEPT = {
    "eqr": 20616848, "chna": 20616850, "waiver_1115": 20616852,
    "mmrc": 20616854, "nursing_home_2567": 20616856,
}
LABEL = {
    "eqr_quality_measure": "quality measures", "eqr_pip": "performance improvement projects",
    "eqr_compliance": "compliance findings", "chna_identified_need": "identified community health needs",
    "chna_strategy": "implementation strategies", "waiver_1115_finding": "evaluation findings",
    "waiver_1115_recommendation": "recommendations", "mmrc_finding": "findings",
    "mmrc_recommendation": "recommendations", "nursing_home_deficiency": "cited deficiencies",
    "nursing_home_plan_of_correction": "plans of correction",
}
SOURCE = {
    "eqr": "U.S. state Medicaid/CHIP managed-care External Quality Review (EQR) technical reports",
    "chna": "U.S. non-profit hospital Community Health Needs Assessments (CHNAs, IRC 501(r)(3))",
    "waiver_1115": "independent evaluations of Medicaid Section 1115 demonstration waivers",
    "mmrc": "U.S. state Maternal Mortality Review Committee (MMRC) reports",
    "nursing_home_2567": "CMS-2567 nursing-home Statements of Deficiency (OCR for scanned pages)",
}


def bump_patch(v: str) -> str:
    try:
        a, b, c = (v or "0.4.0").split(".")
        return f"{a}.{b}.{int(c) + 1}"
    except Exception:
        return "0.4.1"


def main(dataset: str) -> int:
    token = os.environ["ZENODO_TOKEN"]
    P = {"access_token": token}
    H = {"Content-Type": "application/json"}
    z = sorted(glob.glob(f"dist/{dataset}-v*.zip"))[-1]
    fn = os.path.basename(z)
    zf = zipfile.ZipFile(z)
    recs = [json.loads(l) for l in zf.read([n for n in zf.namelist() if n.endswith("records.jsonl")][0]).decode().splitlines() if l.strip()]
    rt = collections.Counter(r.get("record_type") for r in recs)
    ndocs = zf.read([n for n in zf.namelist() if n.endswith("documents.csv")][0]).decode().count("\n") - 1
    unit_key = "hospital_name" if dataset == "chna" else ("facility_name" if dataset == "nursing_home_2567" else "state")
    nunits = len({r.get(unit_key) for r in recs if r.get(unit_key)})
    bd = ", ".join(f"{c:,} {LABEL.get(t, t)}" for t, c in rt.most_common())

    # resolve latest version recid from the concept
    concept = CONCEPT[dataset]
    latest = requests.get(f"{BASE}/records/{concept}", params=P).json()
    latest_recid = latest["id"]
    cur_ver = (latest.get("metadata", {}) or {}).get("version", "0.4.0")
    new_ver = bump_patch(cur_ver)

    valnote = ("A human-validation study is underway; treat as preliminary."
               if dataset == "eqr" else
               "AI-extracted, not yet independently validated — preliminary; filter on the trust score.")
    desc = (
        f"<p><b>Dark Health Data</b>: {SOURCE[dataset]}, extracted via Claude "
        f"<code>claude-haiku-4-5</code> with a verification layer (grounding, neurosymbolic "
        f"constraints, ensemble, conformal gate).</p>"
        f"<p><b>This release (v{new_ver})</b> — expanded national crawl: <b>{len(recs):,} records</b> "
        f"from <b>{ndocs:,} source documents</b> ({nunits:,} {('hospitals' if dataset=='chna' else 'facilities' if dataset=='nursing_home_2567' else 'states/jurisdictions')}): {bd}. "
        f"Every record carries full provenance and a quality/trust score; nothing is imputed or dropped.</p>"
        f"<p>{valnote} Public-record documents only; no PHI. Code (Apache-2.0): "
        f'<a href="https://github.com/sanjaybasu/dark-health-data">github.com/sanjaybasu/dark-health-data</a>. CC0-1.0.</p>'
    )

    r = requests.post(f"{BASE}/deposit/depositions/{latest_recid}/actions/newversion", params=P)
    r.raise_for_status()
    d = requests.get(r.json()["links"]["latest_draft"], params=P).json()
    did = d["id"]
    bucket = d["links"]["bucket"]
    for f in d.get("files", []):
        requests.delete(f"{BASE}/deposit/depositions/{did}/files/{f['id']}", params=P)
    with open(z, "rb") as fh:
        requests.put(f"{bucket}/{fn}", data=fh, params=P).raise_for_status()
    md = dict(d["metadata"])
    md["version"] = new_ver
    md["description"] = desc
    requests.put(f"{BASE}/deposit/depositions/{did}", params=P, json={"metadata": md}, headers=H).raise_for_status()
    pub = requests.post(f"{BASE}/deposit/depositions/{did}/actions/publish", params=P)
    pub.raise_for_status()
    doi = pub.json()["metadata"].get("doi")
    print(f"PUBLISHED {dataset} v{new_ver} | DOI {doi} | {len(recs):,} records, {ndocs:,} docs, {nunits:,} {unit_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
