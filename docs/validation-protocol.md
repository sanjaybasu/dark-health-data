# Validation protocol — establishing extraction accuracy

How we prove the dataset is trustworthy enough to publish. This is the methods backbone
for the data-resource paper's validation sub-study, and the team SOP. It complements the
automatic, label-free checks in `docs/verification.md`; here we measure accuracy against
human-adjudicated truth.

## 1. Sampling (what to label)
- **Stratified random sample** of extracted rows, stratified by state × measure family
  (and by extractor if running an ensemble). Use `dhd sample --stratify state`.
- **Size for tight intervals:** to estimate a field-accuracy proportion with a 95% CI
  half-width ≈ ±5% near p≈0.9, label ≈ 140 rows; ≈ ±3% needs ≈ 350. Target **300–400**
  rows for a credible headline number; more if reporting per-measure accuracy.

## 2. Annotation (how to label)
- **≥2 independent reviewers**, **blinded** to the model's `trust_score`/`qa_flags`
  (avoid anchoring), each judging every sampled row against the source PDF page
  (`prov_source_url`, `prov_page_start`).
- A written **codebook** defining "correct": exact value match (state a rounding
  tolerance, e.g. ±0.1), correct entity attribution (right plan/measure/year/population),
  and distinct codes for **wrong value**, **hallucinated** (not in source), and **mis-mapped**.
- **Adjudicate** disagreements (third reviewer or consensus); report **inter-rater
  agreement** — Cohen's κ, and **Gwet's AC1** (more stable than κ under high agreement,
  which is expected here).

## 3. Metrics (what to report)
- **Field-level precision / recall / F1** with **95% Wilson confidence intervals**;
  overall **row accuracy** (`dhd evaluate` reports accuracy + Wilson CI today).
- **Exact-row match** rate (all fields correct).
- **By stratum:** accuracy by state and by measure family, to expose heterogeneity.

## 4. Recall / omissions (the hard part)
Per-row labeling measures precision, not whether rows were **missed**. To measure recall:
- Sample whole **documents** (not rows); have reviewers **enumerate every true measure**
  in each, then compare to what was extracted → recall = found / true.
- Cheaper proxy: **expected-vs-extracted row counts** per table, and the ensemble
  **omission** signal (rows the 2nd extractor found that the 1st missed).

## 5. Trust calibration (does `trust_score` mean what it says?)
- **Reliability diagram** + **ECE** of `trust_score` vs observed correctness.
- **Risk–coverage curve** and the **conformal gate**: report the threshold and achieved
  coverage at target error α (`dhd evaluate --alpha 0.05 --stratify state`). Use the
  **stratified** gate so the guarantee survives cross-state distribution shift.

## 6. Criterion validity (free, no manual labels)
- Where measures are independently published — **CMS Medicaid/CHIP Adult & Child Core
  Set** — join on (state, measure, year) and report concordance: % within tolerance,
  mean absolute error, and a **Bland–Altman** plot for rates. This externally validates a
  subset without human labeling.

## 7. Reproducibility & integrity
- Fix and **report** the model + version, prompt (hash), `temperature=0`, and cost.
- **Version** the dataset (Zenodo DOI per release); gold labels are tied to identity keys
  so they survive re-extraction.
- **Pre-register** this protocol (e.g., OSF) before labeling, for credibility.
- Frame data-quality dimensions per **Kahn et al. (2016)** — conformance, completeness,
  plausibility — adapted for document extraction.

## 8. Toolkit status
- **Today:** `dhd sample` (stratified) + `dhd evaluate` (row accuracy + Wilson CI,
  risk–coverage curve, conformal calibration overall and per stratum).
- **To add for full rigor** (see ROADMAP): two-rater agreement (κ / AC1), document-level
  recall mode, per-field P/R/F1, and the CMS Core Set criterion-validity join.
