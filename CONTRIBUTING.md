# Contributing

Thanks for helping liberate buried public-health data.

## Ways to contribute

- **Add a connector** for a new buried dataset (CHNAs, MMRC reports, 1115 waivers,
  CMS-2567s, …). See "Adding a new dataset" in [`docs/architecture.md`](docs/architecture.md).
- **Expand source coverage** for an existing dataset by adding entries to
  `registry/sources_<id>.yaml`.
- **Improve extraction quality** (prompts, schema, validation rules).
- **Validation studies**: human-review samples and report precision/recall by field.

## Ground rules

1. **Public records only.** Only ingest documents that are lawfully public. Never
   add anything containing protected health information (PHI) or other restricted
   data.
2. **Provenance is non-negotiable.** Every record must carry full `Provenance` and
   a `QAStatus`. Never impute or silently drop values — flag them.
3. **Don't duplicate.** Before adding a dataset, fill in its `existing_efforts` in
   `registry/datasets.yaml` and check [`docs/non-duplication.md`](docs/non-duplication.md).
4. **Keep the core dependency-light.** New heavy dependencies go behind an optional
   extra so the offline demo keeps working everywhere.

## Dev setup

```bash
pip install -e ".[all,dev]"
make demo      # offline end-to-end
make test      # pytest
make lint      # ruff
```

## Pull requests

- Add/maintain tests (the offline demo path must stay green without network or API
  keys).
- Run `make test` and `make lint` before opening a PR.
- Describe the source documents a new connector targets and link an example.
