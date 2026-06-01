# Roadmap — prioritized

Ordered by value-to-effort. Each item names where to work. The pipeline, verification
layer, and three connectors are built and tested; this is the path from "working
prototype" to "published, citable resource."

## 1. First real extraction (do this first)
- Set `ANTHROPIC_API_KEY`. `cp registry/sources_eqr_live.yaml.example registry/sources_eqr_live.yaml`
  and add 3–5 real state EQR report `url:`s (or a `landing_url:` to crawl).
- `dhd run --dataset eqr --extractor llm --limit 5`. Inspect `data/processed/eqr/`.
- **Acceptance:** real rows with `prov_source_span` populated and sane `trust_score`s.

## 2. Validation sub-study → the paper's key figure
- `dhd sample --dataset eqr --n 100 --stratify state -o gold/eqr.csv`; label `correct` (1/0).
- `dhd evaluate --dataset eqr --gold gold/eqr.csv --alpha 0.05 --stratify state`.
- Paste accuracy + risk–coverage + the calibrated threshold into the (privately maintained) manuscript.
- **Acceptance:** `evaluation_report.json` with per-field accuracy and a coverage curve.

## 3. Grounding hardening for real PDFs
- Ensure the LLM/VLM populate `provenance.source_span` (and `bbox` if available); the
  grounding verifier (`verify/grounding.py`) then catches hallucinations for free.
- Optional: capture bounding boxes from the parser (`pdf.py`) for agentic re-grounding.

## 4. Decorrelated vision ensemble (Qwen)
- Today `extract/openai_compatible.py` cross-checks the *parsed text* with a 2nd model.
- Stronger: add a vision path that renders each page to an image and lets Qwen-VL read
  pixels (true modality decorrelation). Wire as another `--second-extractor`.

## 5. Cross-source corroboration (free external validation)
- Join EQR measures to CMS Medicaid/CHIP **Adult & Child Core Set** published rates on
  (state, measure, year); add as an external-agreement signal + validation table.

## 6. National source coverage
- Build `registry/sources_eqr_live.yaml` to all states; seed the crawler from the CMS
  EQRO contact list. Same for CHNA (hospital list) and MMRC (state reports).

## 7. Publish
- Deposit a versioned release to **Zenodo** (DOI) with the Croissant metadata; flip the
  GitHub repo public; announce to colleagues.

## 8. Paper
- The data-resource manuscript is maintained privately until publication; fill its
  remaining placeholders (validation metrics, DOI, authors). Methods are already written.

## 9. More connectors
- `waiver_1115`, `nursing_home_2567`, then SERFF rate filings / SAMHSA block grants
  (see `registry/datasets.yaml`). Each is one new connector — no pipeline changes.

## Stretch / research
- Swap the bounded-logic core for IBM `lnn` to *learn* soft-constraint weights from gold.
- SMT backend (`z3-solver`) for richer numeric constraints.
- Weak-supervision (Snorkel-style) label model to learn signal fusion from gold.
- Active learning: route the conformal "review" set to humans, retrain fusion, repeat.
