# Desktop setup → public sharing (for Claude Code in VS Code)

This is the operational runbook to take the project from the bundle to a **public,
citable GitHub repo + Zenodo dataset**. (For accuracy/validation, see
`docs/validation-protocol.md`.)

## 0. Get the code onto your machine
```bash
git clone dark-health-data.bundle dark-health-data   # from the bundle you downloaded
cd dark-health-data
```

## 1. Verify it runs (offline, no key)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
make demo && make test         # expect the offline demo + all tests green
```

## 2. Pre-publication hygiene (do NOT skip)
- Confirm no secrets are committed: `git ls-files | grep -iE '\.env$|key|secret'` should be empty. The pipeline keeps API keys in `.env` (gitignored) only.
- `data/raw`, `data/cache`, `data/processed`, and `dist/` are gitignored — extracted data and zips are **not** committed (the dataset is deposited to Zenodo instead).
- Present files for a healthy public repo: `LICENSE` (Apache-2.0, code), `README.md`,
  `CITATION.cff`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/workflows/ci.yml`.

## 3. Create the PUBLIC GitHub repo and push
With the GitHub CLI (`gh auth login` first), from the repo root:
```bash
gh repo create dark-health-data --public --source=. --remote=origin \
  --description "AI-extracted open datasets from buried public-health documents (Medicaid EQR, CHNA, MMRC)" --push
gh repo edit --add-topic medicaid,public-health,open-data,health-equity,nlp,health-services-research
```
(Or, in Claude Code, use the GitHub integration to create the repo + push to `main`.)
If you started from the `.tar.gz` instead of the bundle: `git init && git add -A &&
git commit -m "initial public release" && gh repo create ... --source=. --push`.

## 4. Tag a release (so Zenodo can archive it)
```bash
git tag -a v0.3.0 -m "Dark Health Data v0.3.0" && git push origin v0.3.0
gh release create v0.3.0 --title "v0.3.0" --notes "Pipeline + EQR/CHNA/MMRC connectors + verification layer."
```

## 5. Mint DOIs on Zenodo
- **Software DOI:** sign in to Zenodo with GitHub → *Settings → GitHub* → flip the
  toggle **on** for `dark-health-data` → (re-)publish the release. Zenodo reads
  `.zenodo.json` and mints a versioned DOI for the code.
- **Dataset DOI (separate record, CC0):** upload the dataset zip
  (`dist/eqr-vX.Y.Z.zip`, produced by `python scripts/make_release.py --dataset eqr`)
  as a **new Zenodo upload**, license **CC0-1.0**, type *Dataset*. It already contains
  `DATASET_CARD.md`, `croissant.json`, and a sha256 `MANIFEST.json`.
- Put both DOIs in `README.md` and `CITATION.cff`, then commit.

## 6. Announce
Announce to colleagues (email/Slack/LinkedIn). Link the repo + the dataset DOI.

## 7. State the caveats on the repo (integrity)
The data is **AI-extracted and pending the manual validation sub-study** — say so in the
README and the Zenodo description, and mark the dataset **v0.x / preliminary** until the
validation (per `docs/validation-protocol.md`) is reported. It's all **public-record**
data with **no PHI**.

## Reminders
- **Rotate any API key** you used for extraction (especially if pasted into a chat).
- For more states, populate `registry/sources_eqr_live.yaml` (a verified 24-state list is
  in `…_live.yaml.example`) and re-run; JS-rendered state pages need `render: true` +
  `pip install -e ".[crawl]" && playwright install chromium`.
