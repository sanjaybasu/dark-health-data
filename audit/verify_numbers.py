#!/usr/bin/env python3
"""Re-derive every reportable EQR dataset number (coverage counts, per-state Table)
from the canonical dataset in data/processed/eqr/. Exit non-zero on any mismatch.

Run:  python audit/verify_numbers.py
A number-provenance check: the reported figures are the claim, data/processed/eqr/
is the source of truth.
"""
from __future__ import annotations
import csv, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data" / "processed" / "eqr"

def rows(name):
    with (D / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

measures = rows("eqr_quality_measures.csv")
pips = rows("eqr_performance_improvement_projects.csv")
comp = rows("eqr_compliance_findings.csv")
docs = rows("documents.csv")
records = [json.loads(l) for l in (D / "records.jsonl").open(encoding="utf-8")]

def fnum(r, k):
    try: return float(r.get(k) or "")
    except ValueError: return None

results = []
def check(desc, claimed, derived, exact=True):
    ok = (claimed == derived) if exact else (abs(claimed - derived) <= 0.01)
    results.append((ok, desc, claimed, derived))

# --- headline aggregates ---
all_states = {r["state"] for r in records if r.get("state")}
check("distinct states", 43, len(all_states))
check("reports (documents.csv rows)", 49, len(docs))
check("total records", 25190, len(records))
check("quality measures", 11733, len(measures))
check("PIPs", 2653, len(pips))
check("compliance findings", 10804, len(comp))
check("distinct measure names", 4617, len({m["measure_name"] for m in measures}))
check("state x plan groupings (measures)", 377,
      len({(m["state"], m["plan_name"]) for m in measures}))

# states with >=1 measure row
states_with_measures = {m["state"] for m in measures}
check("states with measure rows", 35, len(states_with_measures))

# mean trust over measure rows (Table 1 total) and over all records (abstract)
mt_meas = [fnum(m, "trust_score") for m in measures if fnum(m, "trust_score") is not None]
mean_meas = round(sum(mt_meas) / len(mt_meas), 2)
check("mean trust (measure rows) ~0.97", 0.97, mean_meas, exact=False)
mt_all = [fnum(r2, "trust_score") for r2 in
          [dict(trust_score=rr.get("trust_score")) for rr in records]
          if fnum(r2, "trust_score") is not None]
mean_all = round(sum(mt_all) / len(mt_all), 2) if mt_all else None
print(f"[info] mean trust over ALL records = {mean_all}")

# hard logical failures (qa_status == fail) -- flagged, not dropped
n_fail = sum(1 for r in records if r.get("qa_status") == "fail")
check("hard logical failures", 158, n_fail)

# review-recommended fraction (~4%)
n_review = sum(1 for r in records if r.get("review_recommended"))
pct_review_meas = round(100 * n_review / len(measures), 1)
pct_review_all = round(100 * n_review / len(records), 1)
print(f"[info] review_recommended n={n_review}; over measures={pct_review_meas}% over all={pct_review_all}%")

# --- Table 1 per-state cells ---
# claimed: (reports, plans, measures, pips, compliance) ; mean trust checked separately
T1 = {
 "AL": (1,6,62,185,493), "AR": (1,4,1436,97,1449), "AZ": (1,10,193,10,53),
 "CA": (1,0,0,0,0),  # report fetched but yielded no extractable records
 "CO": (1,11,127,4,94), "DC": (1,14,275,77,250), "DE": (1,3,227,44,380),
 "FL": (1,0,0,53,0), "GA": (2,2,13,1,64), "HI": (1,10,1235,145,563),
 "IA": (2,13,147,29,20), "ID": (1,1,40,13,55), "IL": (1,0,0,25,58),
 "IN": (1,11,54,0,0), "KY": (1,1,5,0,72), "LA": (1,0,0,13,18),
 "MD": (1,1,10,37,39), "MI": (1,8,130,1,11), "MN": (1,15,723,265,334),
 "MO": (2,32,514,56,734), "MS": (1,0,0,20,8), "NC": (2,5,20,56,101),
 "ND": (1,3,334,41,189), "NE": (1,2,18,16,0), "NH": (1,7,506,23,335),
 "NJ": (1,27,2889,260,2929), "NM": (1,9,120,273,162), "NV": (1,41,791,200,180),
 "NY": (1,23,95,30,14), "OH": (1,0,0,12,70), "OK": (1,0,0,0,48),
 "OR": (1,1,6,33,70), "PA": (1,0,0,14,0), "RI": (2,7,715,142,133),
 "SC": (1,0,0,10,54), "TN": (1,4,73,1,59), "TX": (1,25,119,49,0),
 "UT": (1,37,333,260,778), "VA": (1,19,170,23,15), "VT": (1,4,171,5,78),
 "WA": (1,3,28,2,109), "WI": (1,3,30,8,101), "WV": (1,13,111,102,603),
 "WY": (1,2,13,18,81),
}
docs_by_state = Counter(d["jurisdiction"] for d in docs)
meas_by_state = Counter(m["state"] for m in measures)
pip_by_state = Counter(p["state"] for p in pips)
comp_by_state = Counter(c["state"] for c in comp)
plans_by_state = defaultdict(set)
for m in measures:
    plans_by_state[m["state"]].add(m["plan_name"])

for st, (rep, pl, me, pi, co) in T1.items():
    check(f"T1 {st} reports", rep, docs_by_state.get(st, 0))
    check(f"T1 {st} plans", pl, len(plans_by_state.get(st, set())))
    check(f"T1 {st} measures", me, meas_by_state.get(st, 0))
    check(f"T1 {st} PIPs", pi, pip_by_state.get(st, 0))
    check(f"T1 {st} compliance", co, comp_by_state.get(st, 0))

# --- report ---
fails = [r for r in results if not r[0]]
for ok, desc, claimed, derived in results:
    if not ok:
        print(f"  MISMATCH  {desc}: manuscript={claimed!r}  data={derived!r}")
print(f"\n{len(results)} checks, {len(fails)} mismatch(es).")
sys.exit(1 if fails else 0)
