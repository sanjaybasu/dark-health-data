# What to do next — a sequential guide

Do these in order. Each step links to the detailed doc. Dataset numbers are final and
consistent across the papers: **25 states · 28 reports · 2,763 records** (1,238 measures,
447 PIPs, 1,078 compliance; mean trust 0.99).

## 0. Get it on your machine (VS Code)
```bash
git clone dark-health-data.bundle dark-health-data && cd dark-health-data
code .                                    # open the folder in VS Code (this is what Claude Code acts on)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && make test && make demo     # expect 29 tests passing
```

## 1. Publish to GitHub (public) — paste into Claude Code in VS Code
The full runbook is `docs/desktop-publishing.md`; the paste prompt:
```
Read CLAUDE.md and docs/desktop-publishing.md, then publish this repo publicly:
verify (make test); confirm no secrets are committed; create a PUBLIC GitHub repo
"dark-health-data" and push main (gh repo create dark-health-data --public --source=.
--remote=origin --push); add topics medicaid, public-health, open-data, health-equity, nlp;
tag and create a v0.3.0 release. Then stop and report the repo URL. Guardrails: public-record
data only; no PHI; main branch only — do NOT push to data_profiling; ask before anything destructive.
```

## 2. Deposit the dataset → Zenodo DOIs
- Enable the repo in Zenodo (GitHub toggle) → re-publish the release → **software DOI**.
- Upload `dist/eqr-v0.3.0.zip` as a **new Zenodo record** (type *Dataset*, license **CC0**),
  using the description text provided in the chat thread → **dataset DOI**.
- Paste both DOIs into `README.md`, `CITATION.cff`, and the papers' `‹PENDING›`/`[Zenodo DOI]` slots.

## 3. Run the validation study (the science gate) — `docs/validation-runbook.md`
```bash
# (regenerate data if needed) export ANTHROPIC_API_KEY=...; dhd run --dataset eqr --extractor llm
dhd sample   --dataset eqr --n 300 --stratify state -o gold/eqr.csv
#   → hand gold/eqr.csv + docs/validation-runbook.md to an RA; label `correct` (1/0);
#     ideally a 2nd reviewer labels ~50 overlapping rows for κ.
dhd evaluate --dataset eqr --gold gold/eqr.csv --alpha 0.05 --stratify state
```
Then paste accuracy / 95% CI / κ / coverage into the papers' `‹PENDING›` slots (Abstract + Validation §).

## 4. Email a validation collaborator
Send the short invitation (maintained privately); the validation in Step 3 is the natural
thing to collaborate on / hand to a research assistant.

## 5. Submit the paper
- The manuscript is maintained privately (off this repository) until publication.
- It needs only: validation numbers (Step 3), the Zenodo DOI (Step 2), and the author list.

## 6. Expand coverage & ship updates (ongoing)
- More states: edit `registry/sources_eqr_live.yaml` (24-state template in `…_live.yaml.example`);
  JavaScript-rendered listings need `render: true` + `pip install -e ".[crawl]" && playwright install chromium`.
- Re-run → `python scripts/make_release.py --dataset eqr` → deposit a **new Zenodo version**
  (Zenodo versions share a concept-DOI, so citations stay valid).
- New datasets: CHNA and MMRC connectors already exist (`dhd run --dataset chna|mmrc`);
  `waiver_1115` and `nursing_home_2567` are roadmap stubs in `registry/datasets.yaml`.

## Reminders
- **Rotate the Anthropic API key** — it's no longer needed here.
- Keep the **"AI-extracted, validation pending"** caveat on the repo/Zenodo/paper until Step 3 is reported.
- Orientation map of every file: `START_HERE.md`.
