# Hidden Health Data

**Turning buried public-health documents into open, research-ready datasets.**

A large amount of public-health and healthcare evidence is *theoretically* public
but *practically* unusable for science: it is locked inside hundreds of thousands
of PDFs scattered across state agency, hospital, and regulator websites, with no
common schema and no central index. Researchers call this **"dark data"** — and in
public health its absence falls hardest on underserved populations, because the
groups we most need to study are the ones whose data is least organized
([Dark Data in Public Health, *J Public Health Policy* 2025](https://link.springer.com/article/10.1057/s41271-025-00589-3);
[structural missingness, *PMC* 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8607058/)).

Hidden Health Data is an automated pipeline that **discovers, extracts, validates,
and publishes** these datasets — using LLM-based structured extraction ("AI
scanning") with a strict provenance-and-QA contract so the output is credible for
peer-reviewed research.

## Datasets

Each release is on Zenodo (CC0-1.0). The DOI below is the concept DOI, which resolves to
the latest version. Each release has a `DATASET_CARD.md` with its method, checks, and
limits.

| id | rows are | data DOI | latest version | human review of a random 150-row sample |
|---|---|---|---|---|
| `eqr` | quality measures, performance improvement projects, and compliance findings from Medicaid External Quality Review technical reports | [10.5281/zenodo.20616848](https://doi.org/10.5281/zenodo.20616848) | 0.4.1 | not yet reported |
| `chna` | needs and implementation strategies from hospital Community Health Needs Assessments | [10.5281/zenodo.20616850](https://doi.org/10.5281/zenodo.20616850) | 0.4.8 | not yet reported |
| `waiver_1115` | findings and recommendations from Medicaid Section 1115 demonstration evaluations | [10.5281/zenodo.20616852](https://doi.org/10.5281/zenodo.20616852) | 0.4.0 | not yet reported |
| `mmrc` | findings and recommendations from Maternal Mortality Review Committee reports | [10.5281/zenodo.20616854](https://doi.org/10.5281/zenodo.20616854) | 0.4.0 | not yet reported |
| `nursing_home_2567` | cited deficiencies from nursing-home statements of deficiencies (CMS-2567) | [10.5281/zenodo.20616856](https://doi.org/10.5281/zenodo.20616856) | 0.4.5 | not yet reported |
| `fair_hearing` | Connecticut Medicaid fair-hearing decisions, one row per decision (issue, respondent, outcome) | [10.5281/zenodo.24164995](https://doi.org/10.5281/zenodo.24164995) | 0.1.3 | all fields right in 141 of 150 (94.0%) |
| `mco_contract` | Medicaid managed care contract requirements in 7 states, with the verbatim contract text | [10.5281/zenodo.24178124](https://doi.org/10.5281/zenodo.24178124) | 0.1.0 | all fields right in 135 of 150 (90.0%) |
| `medicaid_advisory` | discussion items from Medicaid advisory committee and beneficiary council minutes in 12 states | [10.5281/zenodo.24180638](https://doi.org/10.5281/zenodo.24180638) | 0.1.0 | all fields right in 134 of 150 (89.3%) |

> **The first dataset: Medicaid External Quality Review (EQR) technical reports.**
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
and the AcademyHealth **Medicaid Data Learning Network**. Hidden Health Data is the
**complement** to these efforts: we extract *quality, compliance, oversight, and
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

`evaluate` reports per-row accuracy and a risk–coverage curve, and calibrates the conformal
gate (a bound on the error rate of auto-accepted rows that holds when new rows resemble
the labeled sample). For the newer datasets, `scripts/review_sheet.py` builds a review
workbook (a stratified row sample plus random pages for missed items) and scores it with
Wilson confidence intervals. New here? See [`CLAUDE.md`](CLAUDE.md) and [`ROADMAP.md`](ROADMAP.md).

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

The pipeline can run the checks below (`src/dark_health_data/verify/`). Which ones ran
for a release, and the human review if there was one, is stated in that release's
`DATASET_CARD.md`. The three newest releases (`fair_hearing`, `mco_contract`,
`medicaid_advisory`) used the domain rules and, where rows carry a quote, grounding,
plus a human review of a random sample; the ensemble and the conformal gate are
optional and were not used for them.

- **Grounding** — the cited quote must appear in the source text (catches invented text;
  it also fails on correct quotes from tables or multi-column layouts).
- **Symbolic constraints** — declarative domain axioms (num ≤ den, rate ∈ [0,100], …).
- **LNN-inspired bounded-logic** — interval-truth contradiction detection with
  per-axiom *explanations* (the neurosymbolic core).
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

## Running the connectors

Every dataset in the table above has a connector and a synthetic fixture, so each runs
offline:

```bash
dhd run --dataset chna              --extractor rule   # hospital community needs (demo)
dhd run --dataset mmrc              --extractor rule   # maternal mortality review (demo)
dhd run --dataset waiver_1115       --extractor rule   # 1115 demonstration evaluations (demo)
dhd run --dataset nursing_home_2567 --extractor rule   # nursing-home 2567 deficiencies (demo)
dhd run --dataset fair_hearing      --extractor rule   # Medicaid fair-hearing decisions (demo)
dhd run --dataset mco_contract      --extractor rule   # MCO contract provisions (demo)
dhd run --dataset medicaid_advisory --extractor rule   # MAC/BAC meeting minutes (demo)
```

The newer datasets use these steps:

- Sources: `scripts/discover_ct_fair_hearings.py` (Connecticut fair-hearing decisions) and
  `scripts/discover_advisory_minutes.py` (advisory committee minutes) list the documents;
  the MCO contract list is in `registry/sources_mco_contract_live.yaml.example`.
- Text: `scripts/prep_text_lightonocr.py` reads the PDF text layer and runs local
  LightOnOCR on pages without a usable one (Apple silicon; `pip install mlx-vlm pymupdf`).
- Extraction: `dhd batch` (Message Batches API).
- Privacy: `scripts/privacy_rewrite.py` removes identifiers that a fair-hearing row still
  states; `scripts/redact_names.py` replaces personal names and individual ages in the
  advisory-minutes rows.
- Review and fixes: `scripts/review_sheet.py build|score`; `scripts/apply_corrections.py`
  applies checked corrections and records the basis of each.
- Release: `scripts/publish_zenodo_new.py` (new record or new version).

Crawling: the crawler names itself in its user agent and never presents a browser's.
robots.txt is checked before a site is crawled (in code by `discover_advisory_minutes.py`;
by hand for the Connecticut decision list and the contract list). The scripts wait
between requests and skip a site that refuses them; they do not work around a 403, a
captcha, or a login.

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

The sources are **public records** that agencies, hospitals, and regulators post. Some
describe people: fair-hearing decisions are redacted by the state before posting, and
meeting minutes record what members of the public say. The datasets keep no names,
addresses, or ID numbers of those people (each card says how this is done and how well
it works). Output is **AI-extracted and should be checked against the source PDF**
(linked per row) before use in analysis or publication. This
project reorganizes public information to make it analyzable; it does not change the
underlying records.

## Cite / license

- Code: Apache-2.0 (`LICENSE`). Derived data: CC0-1.0 (Zenodo; DOIs in the table above).
- Citation metadata in [`CITATION.cff`](CITATION.cff).
- A data-resource manuscript is in preparation and is maintained privately until publication.

See [`docs/landscape.md`](docs/landscape.md) for the literature review of missing
public-health data and the full catalog of candidate datasets.
