# Architecture

## Pipeline

```
                 registry/datasets.yaml         registry/sources_<id>.yaml
                 (catalog of dataset            (where the documents are)
                  families)                                │
                                                           ▼
   ┌──────────┐   ┌────────┐   ┌──────────┐   ┌─────────────────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐
   │ DISCOVER │──▶│ FETCH  │──▶│ PDF/OCR  │──▶│ EXTRACT             │──▶│ VERIFY   │──▶│ CURATE  │──▶│ PUBLISH  │
   │ candidates│   │ cache, │   │ text +   │   │  • LLM (Claude)     │   │ ground · │   │ tidy    │   │ dict +   │
   │ (or crawl)│   │ sha256 │   │ [[PAGE]] │   │  • VLM/Qwen (2nd)   │   │ symbolic·│   │ tables  │   │ card +   │
   │           │   │        │   │ + bbox   │   │  • rule (offline)   │   │ LNN·ens· │   │ + trust │   │ croissant│
   └──────────┘   └────────┘   └──────────┘   └─────────────────────┘   │ conformal│   └─────────┘   └──────────┘
                                                                         └──────────┘
```

Every stage is in `src/dark_health_data/`:

| Stage | Module | Responsibility |
|---|---|---|
| discover | `discovery.py` + connector `.discover()` | registry entries (or landing-page crawl) → `CandidateDoc`s |
| fetch | `fetch.py` | download/local-read, content-address by sha256, write provenance sidecar |
| pdf/ocr | `pdf.py` | text + `[[PAGE n]]` markers; pdfplumber→pymupdf fallback; optional OCR |
| extract | `extract/{llm,openai_compatible,base}.py` | text → typed, provenance-stamped records (Claude / local Qwen / rule) |
| verify | `verify/` | grounding · symbolic · LNN · ensemble · fusion · conformal gate → `trust_score`, `review_recommended` (see [verification.md](verification.md)) |
| curate | `curate.py` | tidy CSV/Parquet tables + lossless `records.jsonl` |
| publish | `publish.py` | data dictionary, dataset card, Croissant (FAIR) metadata |

Orchestration and CLI: `pipeline.py` (`dhd run --dataset eqr ...`).

## The provenance & QA contract

Defined in `models.py`. Every record subclasses `ExtractionRecord` and therefore
carries a `Provenance` (source document sha256, URL, page span, method, model,
confidence, timestamp) and a `QAStatus` (`pass`/`warn`/`fail`) with human-readable
`qa_flags`. This is the part that makes the output usable for science:

- **Traceable** — every value links back to a page in a hashed source document.
- **Auditable** — automated checks (numerator ≤ denominator, 0 ≤ percent ≤ 100,
  printed-rate vs numerator/denominator agreement, duplicate analytic grain) are
  recorded, not silently applied.
- **Honest** — we never impute or delete; we flag and let the researcher decide.

## Two extractors, one schema

- **`RuleExtractor`** — deterministic parser of a canonical text layout. Zero
  dependencies; powers the offline demo, CI, and serves as a labelled fixture. It
  is *not* a general PDF parser.
- **`LLMExtractor`** — Claude structured extraction via tool-use, which forces
  schema-valid JSON, with **prompt caching** on the (large, static) instructions +
  schema so multi-document / multi-chunk runs are cheap. This is what generalizes
  to real, heterogeneous PDFs.

Both emit the same typed records, so nothing downstream knows or cares which ran.

## Adding a new dataset (one connector)

1. Implement a `Connector` subclass (see `connectors/eqr.py`) with:
   `discover()`, `extraction_schema()`, `extraction_instructions()`,
   `records_from_payload()`, and `parse_rule_based()`.
2. Add record models to `models.py` and register their table in `RECORD_TABLE`.
3. Register the connector in `connectors/__init__.py`.
4. Add a `registry/sources_<id>.yaml` and a catalog entry in `datasets.yaml`.

No pipeline code changes.

## Design choices worth knowing

- **Content-addressed cache.** Documents are immutable public records; we key the
  cache (and the stable `document_id`) on the sha256 of the bytes, so re-runs never
  re-download and provenance is stable.
- **Dependency-light core.** `pydantic` + `pyyaml` + `requests` only; PDF, OCR,
  LLM, and Parquet are optional extras. The offline demo runs anywhere.
- **Backend resilience.** PDF text extraction tries pdfplumber, then PyMuPDF, so a
  broken native dependency in one environment doesn't stop the pipeline.
