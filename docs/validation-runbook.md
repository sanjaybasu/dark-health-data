# Validation runbook (for a research assistant)

**Goal:** measure how accurate the AI-extracted EQR data is, by hand-checking a random
sample against the original PDFs. No coding beyond two commands. Budget ~3–5 hours for 300
rows. Output: an accuracy estimate with a 95% CI + a risk–coverage curve the team can cite.

## Setup (one time)
```bash
git clone <repo-url> dark-health-data && cd dark-health-data
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
# you also need the produced dataset in data/processed/eqr/ (ask Sanjay for the run, or
# run `dhd run --dataset eqr --extractor llm` with an ANTHROPIC_API_KEY)
```

## Step 1 — draw the sample
```bash
dhd sample --dataset eqr --n 300 --stratify state -o gold/eqr_sample.csv
```
This writes a spreadsheet of 300 random rows (spread across states), each with the
extracted values, a `trust_score`, the **source URL** (`prov_source_url`), the **page**
(`prov_page_start`), and an empty **`correct`** column for you to fill.

## Step 2 — label each row (the actual work)
Open `gold/eqr_sample.csv`. For each row:
1. Open `prov_source_url` and go to page `prov_page_start`.
2. Find the plan + measure named in the row.
3. Put **`1`** in `correct` if the extracted value matches the report (allow rounding,
   e.g. ±0.1), **`0`** if it doesn't. Leave blank only if you truly can't find it.

**Codebook — count it `0` (wrong) if any of these:** the number differs; it's attributed
to the wrong plan/measure/year/population; the value isn't actually in the report
(hallucinated); a count was reported as a percent (or vice-versa).

**Year:** `reporting_year` should be the **measurement year** shown for that rate (the
column/section header — "MY 2023", "FFY 2023", etc.), not the report's cover/publication
year. If the row shows the publication year but the table's measurement year is earlier,
count it `0`.

**Tips:** do a few obvious high-`trust_score` rows and a few low ones first to calibrate
your eye. Jot any systematic patterns (e.g., "denominators off for state X") in a notes
column — those are gold for improving extraction. For a kappa/agreement statistic, have a
**second reviewer independently label ~50 of the same rows**.

## Step 3 — score it
```bash
dhd evaluate --dataset eqr --gold gold/eqr_sample.csv --alpha 0.05 --stratify state
```
Prints overall accuracy + **95% CI**, and (in `data/processed/eqr/evaluation_report.json`)
a **risk–coverage curve** and a calibrated trust threshold — i.e. "rows with trust ≥ T have
≤ 5% error," which lets us auto-accept the reliable rows and review the rest.

## What to hand back
- The filled `gold/eqr_sample.csv`.
- The printed accuracy + CI and `evaluation_report.json`.
- Your notes on systematic error patterns (+ the second reviewer's 50 rows, if done).

That's the whole study. It's what turns "AI-extracted, preliminary" into a validated,
citable dataset.
