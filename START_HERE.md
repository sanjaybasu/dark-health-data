# START HERE — what's what

Two downloads contain **everything**:

1. **`dark-health-data.bundle`** — the entire repository (code + all docs + papers).
   Open it with: `git clone dark-health-data.bundle dark-health-data`
2. **`eqr-v0.3.0.zip`** — the dataset (25 states, 2,763 records, CC0) for Zenodo.

Everything below is a file *inside the bundle* unless noted.

## 1. Put it in a public repo + post to Zenodo (for Claude Code in VS Code)
- **`docs/desktop-publishing.md`** — step-by-step: create the PUBLIC GitHub repo → tag a
  release → mint Zenodo DOIs (software + the CC0 dataset) → announce.
- **`CLAUDE.md`** — primes Claude Code on conventions; paste the kickoff prompt (in the
  chat) after opening the folder in VS Code.
- `ROADMAP.md` — prioritized next steps.

## 2. Manuscript
- The data-resource manuscript is maintained privately (off this repository) until
  publication; validation results are inserted there once the sub-study completes.

## 3. All datasets + code
- **`eqr-v0.3.0.zip`** (separate download) — tidy CSVs + `records.jsonl` + data dictionary
  + dataset card + Croissant metadata + sha256 manifest. The Zenodo deposit.
- The **bundle** is all the code; regenerate the data anytime with
  `dhd run --dataset eqr --extractor llm` (sources in `registry/sources_eqr_live.yaml.example`).

## 4. The SOTA / novel validation methods
- **`docs/verification.md`** — the verification architecture: source **grounding**,
  **symbolic** constraints, an **LNN-inspired neurosymbolic** contradiction engine,
  **heterogeneous-ensemble** cross-checking, and a **conformal** selective-acceptance gate
  (the novel contribution).
- **`docs/validation-protocol.md`** — the accuracy study design (stratified sampling,
  dual blinded review + κ, P/R/F1 with Wilson CIs, recall, calibration, criterion validity).
- **`docs/validation-runbook.md`** — RA-friendly 2-command version to hand to a student.

## Also inside
`docs/landscape.md` (literature review), `docs/non-duplication.md` (vs. Cornell Atlas/T-MSIS),
`docs/architecture.md` and `docs/verification.md`.

> Caveat to keep everywhere: the dataset is **AI-extracted and preliminary** until the
> validation study is done. Public-record data only; no PHI.
