#!/usr/bin/env python3
"""Choose which MCO contract documents to extract, by explicit per-state rules.

Reads data/cache/mco_contract_links.json (from discover_mco_contracts.py) and writes
registry/sources_mco_contract_live.yaml.example. Rule: one copy of each base contract
and amendment per state (plan-specific copies of a model contract are near-identical, so
one plan's series stands in for the rest). A year is set only when the file name or link
text states it; `scripts/date_mco_contracts.py` fills the rest from the document itself.

Usage: python scripts/select_mco_contracts.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

LINKS = json.loads(Path("data/cache/mco_contract_links.json").read_text().replace("\\u200b", ""))
OH_BASE = ("https://dam.assets.ohio.gov/image/upload/medicaid.ohio.gov/Providers/"
           "ProviderTypes/Managed%20Care/Provider%20Agreements/")
PUBLISHER = {
    "WA": "Washington State Health Care Authority",
    "VA": "Virginia Department of Medical Assistance Services",
    "OH": "Ohio Department of Medicaid",
    "KY": "Kentucky Department for Medicaid Services",
    "NC": "North Carolina Department of Health and Human Services",
    "AZ": "Arizona Health Care Cost Containment System",
    "PA": "Pennsylvania Department of Human Services",
}


def doc(title, url, year, kind):
    return {"title": title, "type": kind, "year": year, "url": url}


def kind_of(text: str) -> str:
    return "amendment" if re.search(r"amend|mod\d|change", text, re.I) else "signed contract"


def select() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    out["WA"] = [doc("Apple Health Integrated Managed Care (IMC) contract",
                     x["url"], 2026, "model contract")
                 for x in LINKS["WA"] if x["url"].endswith("/imc-medicaid.pdf")]
    va = []
    for x in LINKS["VA"]:
        if "foster" in x["url"].lower():
            continue
        m = re.search(r"fy[-_ ]?(20)?(\d{2})", x["url"], re.I)
        yr = 2000 + int(m.group(2)) if m else (2027 if "change" in x["url"] else None)
        va.append(doc(x["text"] or Path(x["url"]).stem, x["url"], yr, kind_of(x["url"] + x["text"])))
    out["VA"] = va
    oh = [("2014_07", "ManagedCare-PA-201407.pdf", 2014), ("2015_07", "ManagedCare-PA-201507.pdf", 2015),
          ("2016_07", "ManagedCare-PA-201607.pdf", 2016), ("2017_01", "ManagedCare-PA-201701.pdf", 2017),
          ("2018_07", "ManagedCare-PA-201807.pdf", 2018), ("2023_07", "2023_07_MCO_Final.pdf", 2023),
          ("2024_07", "2024_07_MCO_Final.pdf", 2024), ("2025_07", "2025_07_MCO_Final.pdf", 2025)]
    out["OH"] = [doc(f"Ohio Medicaid MCO Provider Agreement {k}", OH_BASE + f, y, "model contract")
                 for k, f, y in oh]
    ky = []
    for x in LINKS["KY"]:
        name = Path(x["url"]).name
        if "aetna" not in name.lower():
            continue
        y = 2025 if name.startswith("CY2526") else (2021 if name.startswith("2021") else None)
        ky.append(doc(f"Kentucky MCO contract (Aetna Better Health of KY): {x['text']}".strip(),
                      x["url"], y, kind_of(name + x["text"])))
    out["KY"] = ky
    out["NC"] = [doc("NC Medicaid PHP contract 30-190029: "
                     + re.sub(r"https?://[^/]+/|/download.*$", "", x["url"]),
                     x["url"], None, kind_of(x["text"] + x["url"]))
                 for x in LINKS["NC"] if x["text"].startswith("Contract #30-190029")]
    # AZ posts each plan's signature-page amendment plus one combined model amendment
    # ("Amendment N for UFC//M for HC ...") that carries the contract text: keep those.
    az = []
    for x in LINKS["AZ"]:
        u = x["url"]
        if "/ACC/" not in u or "for UFC" not in x["text"] or not u.lower().endswith(".pdf"):
            continue
        m = re.search(r"EFF(?:DATE)?(\d{2})(\d{2})(\d{2})", u, re.I)
        az.append(doc(x["text"], u, 2000 + int(m.group(3)) if m else None, "amendment"))
    out["AZ"] = az
    out["PA"] = [doc(x["text"], x["url"], int(re.match(r"(20\d\d)", x["text"]).group(1)),
                     "signed contract")
                 for x in LINKS["PA"] if "HealthChoices Agreement" in x["text"]]
    return out


def main() -> None:
    sel = select()
    sources = [{"state": st, "publisher": PUBLISHER[st], "contracts": docs}
               for st, docs in sel.items()]
    path = Path("registry/sources_mco_contract_live.yaml.example")
    path.write_text("# Selected by scripts/select_mco_contracts.py; years filled by "
                    "scripts/date_mco_contracts.py where null.\n"
                    + yaml.safe_dump({"sources": sources}, sort_keys=False, width=200))
    for st, docs in sel.items():
        print(st, len(docs), "docs;", sum(d["year"] is None for d in docs), "without year")


if __name__ == "__main__":
    main()
