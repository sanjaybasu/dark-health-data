# Dark Health Data

**Turning buried public-health documents into open, research-ready datasets.**

A large amount of public-health and healthcare evidence is *theoretically* public
but *practically* unusable for science: it is locked inside hundreds of thousands
of PDFs scattered across state agency, hospital, and regulator websites, with no
common schema and no central index. Researchers call this **"dark data"** — and in
public health its absence falls hardest on underserved populations, because the
groups we most need to study are the ones whose data is least organized
([Dark Data in Public Health, *J Public Health Policy* 2025](https://link.springer.com/article/10.1057/s41271-025-00589-3);
[structural missingness, *PMC* 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8607058/)).

Dark Health Data is an automated pipeline that **discovers, extracts, validates,
and publishes** these datasets — using LLM-based structured extraction ("AI
scanning") with a strict provenance-and-QA contract so the output is credible for
peer-reviewed research.

> **First release — Medicaid External Quality Review (EQR) technical reports.**
> Every state with Medicaid managed care must publish an annual EQR technical
> report (validated quality measures, performance improvement projects, and
> compliance reviews) under [42 CFR 438.350+](https://www.medicaid.gov/medicaid/quality-of-care/medicaid-managed-care-quality/quality-of-care-external-quality-review).
> They must be public — yet there is **no central, machine-readable repository**,
> a gap MACPAC itself flagged in [March 2025](https://www.macpac.gov/wp-content/uploads/2025/03/MACPAC_March-2025-Chapter-1.pdf).
> This is the textbook "public but inaccessible" dataset, and it sits squarely on
> Medicaid and underserved populations.

## Why this isn't a duplicate

The major modern Medicaid data efforts are built on **structured claims** — the
T-MSIS Analytic Files (TAF) — to produce *spending and utilization* measures. That
includes Cornell's **Medicaid Policy Impact Initiative / Medicaid Atlas**
([Weill Cornell + BU, 2026](https://news.weill.cornell.edu/news/2026/04/grant-supports-efforts-to-create-atlas-of-medicaid-spending))
and the AcademyHealth **Medicaid Data Learning Network**. Dark Health Data is the
**complement, not a competitor**: we extract *quality, compliance, oversight, and
community-needs* information from **narrative regulatory documents** that claims
data structurally does not contain (managed-care plan payments are even redacted
in TAF). Our plan-level quality records are designed to *link to* their plan-level
spending records. See [`docs/non-duplication.md`](docs/non-duplication.md) for a
source-by-source comparison.

## Quickstart

```bash
# 1) Offline demo — no API key, no network, no heavy deps (pydantic + pyyaml)
pip install -e .
make demo            # or: python scripts/run_demo.py

# 2) Process real reports with the AI extractor
pip install -e ".[all]"          # adds pdf backends, anthropic, parquet
cp registry/sources_eqr_live.yaml.example registry/sources_eqr_live.yaml
export ANTHROPIC_API_KEY=sk-...
dhd run --dataset eqr --extractor llm
```

The demo runs the full pipeline on synthetic fixtures and prints the QA flags it
catches (a planted numerator>denominator error, a printed-rate disagreement, and a
duplicate row), then writes tidy CSVs, a data dictionary, a dataset card, and a
[Croissant](http://mlcommons.org/croissant/) metadata file to `data/processed/eqr/`.

```bash
dhd list                              # dataset families in the registry
dhd run --dataset eqr --extractor rule   # offline, deterministic
dhd run --dataset eqr --extractor llm --limit 5   # AI extraction, first 5 docs
```

### Measure accuracy & calibrate the review gate

```bash
dhd sample   --dataset eqr --n 100 --stratify state -o gold/eqr.csv   # draw a sample to label
#   ... fill the `correct` column (1/0) by checking rows against their source ...
dhd evaluate --dataset eqr --gold gold/eqr.csv --alpha 0.05 --stratify state
```

`evaluate` reports per-row accuracy + a risk–coverage curve and calibrates the conformal
gate, so you can state a guaranteed auto-accept error rate — the validation sub-study a
data-resource paper needs. New here? See [`CLAUDE.md`](CLAUDE.md) and [`ROADMAP.md`](ROADMAP.md).

## What you get out

A tidy, **long** table — one row per `(state, plan, measure, year, population)` —
plus tables for performance improvement projects and compliance findings. Every
row carries:

- **Provenance**: `prov_source_document_id` (sha256), `prov_source_url`,
  `prov_page_start`, `prov_method` (`llm`/`rule`/`manual`), `prov_model_name`,
  `prov_confidence`, `prov_extracted_at`.
- **Quality**: `qa_status` (`pass`/`warn`/`fail`) and human-readable `qa_flags`
  (e.g. numerator>denominator, rate/denominator disagreement, duplicate grain).
- **Trust**: a fused `trust_score` in [0,1] and `review_recommended` flag from the
  verification layer below.

Nothing is imputed or dropped — issues are *flagged* so researchers can filter and
audit. See [`docs/architecture.md`](docs/architecture.md) and the generated
`DATA_DICTIONARY.md`.

## Verification (the trust layer)

Every value is checked, then what can't be auto-trusted is routed to a human — so the
output is defensible for research. Implemented in `src/dark_health_data/verify/`:

- **Grounding** — the value must appear in its cited source span (catches hallucinations).
- **Symbolic constraints** — declarative domain axioms (num ≤ den, rate ∈ [0,100], …).
- **LNN-inspired bounded-logic** — interval-truth contradiction detection with
  per-axiom *explanations* (your neurosymbolic core).
- **Ensemble** — a decorrelated 2nd extractor (e.g. local **Qwen**) cross-checks each
  field; disagreements and omissions are surfaced.
- **Conformal gate** — turns trust scores into auto-accept vs. review with a
  finite-sample error guarantee (stratified per state to survive distribution shift).

```bash
# use a local Qwen (via Ollama/vLLM) as a 2nd expert for the ensemble verifier
export OHD_VLM_BASE_URL=http://localhost:11434/v1 OHD_VLM_MODEL=qwen2.5-vl
dhd run --dataset eqr --extractor llm --second-extractor vlm
```

Full SOTA design and extension paths (vision modality, IBM `lnn`, SMT, weak
supervision, gold-set evaluation) are in [`docs/verification.md`](docs/verification.md).

## Architecture in one line

```
discover → fetch (cache, hash) → pdf/ocr → extract (LLM | rule) → validate → curate → publish
```

A source-agnostic core with **pluggable connectors**. Adding a new buried dataset
(CHNAs, maternal mortality review reports, 1115 waivers, nursing-home 2567s …) means
writing *one* connector, not touching the pipeline. The roadmap of dataset families
lives in [`registry/datasets.yaml`](registry/datasets.yaml).

## Roadmap

| id | dataset | status |
|---|---|---|
| `eqr` | Medicaid External Quality Review technical reports | **active** |
| `chna` | Hospital Community Health Needs Assessments | **active** |
| `mmrc` | Maternal Mortality Review Committee reports | **active** |
| `waiver_1115` | Medicaid 1115 demonstration waivers + evaluations | planned |
| `nursing_home_2567` | Nursing-home statements of deficiencies (CMS-2567) | planned |

```bash
dhd run --dataset chna --extractor rule   # hospital community needs (demo)
dhd run --dataset mmrc --extractor rule   # maternal mortality review (demo)
```

For EQR, the connector can also **crawl a state landing page** for report PDFs
(see `landing_url` in `registry/sources_eqr_live.yaml.example`) instead of listing
each PDF by hand. For states whose report listings are **JavaScript-rendered**, add
`render: true` to the source entry and install the headless-browser extra:

```bash
pip install -e ".[crawl]" && playwright install chromium
```

The connector then renders the page (Playwright) before extracting links, with
`landing_max` capping how many (newest-first) it takes.

## Responsible use

These are **public-record** documents; the derived dataset contains **no protected
health information (PHI)**. Output is **AI-extracted and should be validated against
the source PDF** (linked per row) before use in analysis or publication. This
project reorganizes public information to make it analyzable; it does not change the
underlying records.

## Cite / license

- Code: Apache-2.0 (`LICENSE`). Derived data: intended for CC0-1.0 release.
- Citation metadata in [`CITATION.cff`](CITATION.cff).
- A data-resource manuscript is in preparation and is maintained privately until publication.

See [`docs/landscape.md`](docs/landscape.md) for the literature review of missing
public-health data and the full catalog of candidate datasets.
