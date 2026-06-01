#!/usr/bin/env python3
"""Figure 1 for the Data Resource Profile: records by state and type.

Horizontal stacked bars (quality measures / PIPs / compliance), sorted by total.
Driven by the canonical curated CSVs in data/processed/eqr/, so it matches Table 1.

    python scripts/make_coverage_figure.py
Outputs paper/figures/coverage_by_state.png (300 dpi) + .pdf (vector).
"""
from __future__ import annotations
import csv
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data" / "processed" / "eqr"
OUT = ROOT / "paper" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

def col(name, field):
    with (D / name).open(encoding="utf-8") as fh:
        return Counter(r[field] for r in csv.DictReader(fh))

measures = col("eqr_quality_measures.csv", "state")
pips = col("eqr_performance_improvement_projects.csv", "state")
comp = col("eqr_compliance_findings.csv", "state")
states = sorted(set(measures) | set(pips) | set(comp),
                key=lambda s: measures[s] + pips[s] + comp[s])  # ascending -> largest on top

# Okabe-Ito colorblind-safe palette
C_MEAS, C_PIP, C_COMP = "#0072B2", "#E69F00", "#009E73"

m = [measures[s] for s in states]
p = [pips[s] for s in states]
c = [comp[s] for s in states]

plt.rcParams.update({"font.family": "sans-serif",
                     "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
                     "font.size": 8, "axes.labelsize": 9})
fig, ax = plt.subplots(figsize=(6.5, 7.0))
y = range(len(states))
ax.barh(y, m, color=C_MEAS, label="Quality measures")
ax.barh(y, p, left=m, color=C_PIP, label="Performance improvement projects")
ax.barh(y, c, left=[a + b for a, b in zip(m, p)], color=C_COMP, label="Compliance findings")

ax.set_yticks(list(y))
ax.set_yticklabels(states)
ax.set_xlabel("Number of extracted records")
ax.set_ylabel("State")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
# total label at bar end
for i, s in enumerate(states):
    tot = measures[s] + pips[s] + comp[s]
    ax.text(tot + 4, i, str(tot), va="center", fontsize=6.5, color="#333333")
ax.legend(frameon=False, loc="lower right", fontsize=7.5)
ax.set_title("EQR records by state and type (v0.3.0; 25 states, 2,763 records)",
             fontsize=9, loc="left")
fig.tight_layout()

png = OUT / "coverage_by_state.png"
pdf = OUT / "coverage_by_state.pdf"
fig.savefig(png, dpi=300, bbox_inches="tight")
fig.savefig(pdf, bbox_inches="tight")
total = sum(m) + sum(p) + sum(c)
print(f"wrote {png} and {pdf}")
print(f"states={len(states)}  measures={sum(m)}  pips={sum(p)}  compliance={sum(c)}  total={total}")
