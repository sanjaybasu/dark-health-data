# CLAUDE.md — working in this repo

Dark Health Data turns *buried* public-health documents (PDFs) into open,
research-ready datasets via AI extraction, with a verification layer that makes the
output trustworthy. Read this before editing; it encodes the conventions.

## Setup & checks (run these)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
make demo     # offline end-to-end (no keys); or: dhd run --dataset eqr --extractor rule
make test     # pytest (must stay green with no network / no API key)
make lint     # ruff
```

**Definition of done for any change:** `make test` and `make lint` pass, and the
offline demo still runs. The offline path must never require network or API keys.

## Architecture (where things live)

`src/dark_health_data/`
- `pipeline.py` — orchestration + CLI (`dhd run|list|sample|evaluate`)
- `models.py` — pydantic records; the **Provenance + QA + trust** contract (the backbone)
- `connectors/` — one file per dataset (`eqr`, `chna`, `mmrc`); declares schema, prompts,
  rule parser, `constraints()`, `ensemble_key`, `identity_columns`
- `extract/` — `rule` (offline), `llm` (Claude), `openai_compatible` (local Qwen/vLLM)
- `verify/` — grounding, symbolic, LNN-inspired logic, ensemble, fusion, conformal gate
- `evaluation.py` — gold sampling + accuracy + risk–coverage + conformal calibration
- `fetch.py` `pdf.py` `crawl.py` `curate.py` `publish.py` `registry.py`

`registry/` — `datasets.yaml` (catalog) + `sources_<id>.yaml` (where docs are)
`docs/` — `architecture.md`, `verification.md`, `landscape.md`, `non-duplication.md`
`paper/` — data-resource manuscript (kept locally, gitignored; not published pre-acceptance)

## Non-negotiable conventions

1. **Public records only. No PHI, ever.** Only ingest lawfully public documents.
2. **Provenance is mandatory.** Every record carries `Provenance` + `QAStatus`; LLM/VLM
   extractions should populate `provenance.source_span` so grounding can verify them.
3. **Flag, never impute or drop.** Problems become `qa_flags` / low `trust_score` /
   `review_recommended` — the data is preserved.
4. **Dependency-light core.** New heavy deps go behind an optional extra in
   `pyproject.toml` and must be imported lazily; the demo runs on pydantic+pyyaml only.
5. **Don't duplicate.** Before adding a dataset, fill `existing_efforts` in
   `registry/datasets.yaml` and check `docs/non-duplication.md`.
6. **One source of truth for logic.** Domain rules live in a connector's `constraints()`
   and feed both the symbolic verifier and the LNN engine — don't fork them.

## Adding a dataset connector

Implement a `Connector` (see `connectors/eqr.py`): `discover`, `extraction_schema`,
`extraction_instructions`, `records_from_payload`, `parse_rule_based`, `constraints`,
`ensemble_key`, `identity_columns`, `record_models`. Add record models to `models.py`
+ register their table in `RECORD_TABLE`; register the connector in
`connectors/__init__.py`; add `registry/sources_<id>.yaml` + a `datasets.yaml` entry;
add a synthetic fixture + tests. No pipeline changes needed.

## See also

- `ROADMAP.md` — prioritized next steps.
- `docs/desktop-publishing.md` — step-by-step to create a **public GitHub repo + Zenodo
  DOIs** and announce (the operational publishing runbook).
- `docs/validation-protocol.md` — the accuracy/validation sub-study design.
- `docs/validation-runbook.md` — RA-friendly 2-command validation guide (hand to a student).
