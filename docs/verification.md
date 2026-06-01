# Verification: making AI-extracted data trustworthy

Trust comes from **making each value checkable, then routing what you can't check.**
Two levers, both implemented in `dark_health_data/verify/`:

1. **Cheap, label-free verifiers** — grounding, symbolic constraints, an
   LNN-inspired contradiction engine, and ensemble agreement. No gold labels;
   deterministic and explainable.
2. **Calibrated selective acceptance** — fuse the signals into a trust score, then a
   conformal gate auto-accepts what's provably reliable and routes the rest to a
   human, with a finite-sample error guarantee.

```
parse (Qwen/MinerU/…) → extract (Claude ‖ Qwen) → [grounding · symbolic · LNN · ensemble] → fuse → conformal gate → {auto-accept | human review}
```

## Layer 0 — parsing fidelity

Fewer extraction errors start with better parsing. Use a grounding-capable VLM that
returns **bounding boxes** (so a value can be re-checked against pixels). Current SOTA
(open, locally runnable): **Qwen3-VL / Qwen2.5-VL**, **MinerU2.5**, **dots.ocr**,
**olmOCR**; benchmark on **OmniDocBench** (esp. table-structure / TEDS). `pdf.py`
already inserts `[[PAGE n]]` markers; extend it to capture spans/boxes from these tools.

## Layer 1 — extraction that's verifiable by construction

- **Structured/constrained decoding** — `extract/llm.py` uses Anthropic tool-use to
  force schema-valid JSON. This guarantees *structure*, not *truth*.
- **Grounded extraction** — populate `Provenance.source_span` (and `bbox`) with the
  verbatim text each value came from. `verify/grounding.py` then deterministically
  checks the span occurs in the source and contains the value. A cited span that is
  absent, or omits the number it supposedly supports, is a **hard failure** — caught
  with zero labels. This is the highest-ROI upgrade for real (LLM/VLM) runs.

## Layer 2 — post-extraction verification

| Verifier | Module | What it catches | Labels? |
|---|---|---|---|
| **Symbolic constraints** | `verify/constraints.py` | num>den, rate∉[0,100], rate≠num/den, duplicate grain, subgroup>overall | none |
| **LNN-inspired logic** | `verify/lnn.py` | logical contradiction among axioms, with per-axiom attribution | none |
| **Grounding** | `verify/grounding.py` | hallucinated / unsupported values | none |
| **Ensemble** | `verify/ensemble.py` | disagreement between decorrelated extractors; omissions (recall) | none |

Connectors declare their axioms in `Connector.constraints()` (see `connectors/eqr.py`),
so the same logic feeds both the symbolic verifier and the LNN engine — one source of truth.

### The LNN-inspired engine (neurosymbolic core)

`verify/lnn.py` is a compact engine in the spirit of **Logical Neural Networks**
(Riegel et al., 2020): truth values are **intervals** `[lower, upper]` over [0,1],
connectives use **Łukasiewicz** real-valued logic, and *asserting* that a record must
be valid raises its lower bound — so a **contradiction** appears exactly when the
asserted lower bound exceeds the data-implied upper bound. We initialize each domain
axiom's atom from the data (`True→[1,1]`, `False→[0,0]`, N/A→`[0,1]`), conjoin them, and
assert validity; a contradiction flags the record and names the violated axiom.

Why this matters for a *research* dataset: every flag is **explainable** (it traces to
a violated axiom, not a black-box score) and **label-free** (hard logic needs no
ground truth). What it does *not* solve: **omissions** (a missed row) — that's the
ensemble/coverage job.

**Extension paths** (drop-in): swap the bounded-logic core for IBM's `lnn` package to
*learn* soft-constraint weights from a gold set; or discharge numeric constraints with
an SMT solver (`pip install dark-health-data[verify]` → `z3-solver`).

### Ensemble (the "mixture of experts")

Run a second, **decorrelated** extractor and reconcile per field:

```bash
# local Qwen as the 2nd expert (Ollama or vLLM serving an OpenAI-compatible API)
export OHD_VLM_BASE_URL=http://localhost:11434/v1   # Ollama
export OHD_VLM_MODEL=qwen2.5-vl
dhd run --dataset eqr --extractor llm --second-extractor vlm
```

`verify/ensemble.py` matches records on `Connector.ensemble_key`, compares
`Connector.ensemble_fields`, and reports disagreements (a confidence signal) and
**omissions** (records the 2nd model found that the 1st missed — a recall check).
Ensembles only help if errors are uncorrelated, so pair *different model families*;
the strongest version further decorrelates by **modality** — let the VLM read the page
*image*, not the parsed text (a documented next step for `openai_compatible.py`).

## Layer 3 — selective acceptance with a guarantee

`verify/signals.py` fuses signals into a `trust_score` (label-free: weighted geometric
mean, with any hard failure clamped low). `verify/conformal.py` then turns scores into
a decision: given a small labeled calibration set and target risk `alpha`, it picks the
**largest-coverage** threshold whose finite-sample (Hoeffding) upper bound on the
accepted error rate stays ≤ `alpha`; everything below goes to review.

```python
from dark_health_data.verify import ConformalGate
gate = ConformalGate(alpha=0.05, delta=0.05,
                     group_fn=lambda r: r.state).calibrate(calibration)
report = verify_records(records, connector=conn, doc_texts=texts, gate=gate)
```

**Caveat we take seriously:** plain conformal assumes exchangeability, which **breaks
across states/years**. Use the **stratified ("Mondrian")** mode (`group_fn` per state
or measure) so each stratum keeps its guarantee, and monitor coverage as states are
added. Without a gate, the pipeline falls back to a transparent trust threshold.

## Layer 4 — evaluating the verifier (paper-grade)

The piece that needs humans, and the one that makes it publishable:

1. Draw a **stratified random sample**; double-key it to gold (report inter-annotator agreement).
2. Report **per-field precision/recall/F1** and a **risk–coverage curve** (accuracy vs.
   fraction auto-accepted).
3. **Cross-source corroboration** for free external validation: join EQR measures to the
   CMS Medicaid/CHIP **Adult & Child Core Set** published rates on (state, measure, year).
4. Use the gold labels to **calibrate** the conformal gate and, optionally, **learn** the
   fusion/soft-constraint weights (weak-supervision label model, or the IBM LNN).

## How each piece runs today

- Label-free verifiers (symbolic + LNN + grounding) run **by default** in `dhd run` and
  annotate every row with `trust_score`, `review_recommended`, and explanatory `qa_flags`.
- Ensemble runs when you pass `--second-extractor`.
- The conformal gate is calibrated from a labeled gold sample via the CLI:
  `dhd sample` draws a stratified sample to label, and `dhd evaluate` computes per-row
  accuracy + a risk–coverage curve and calibrates the gate (overall and per stratum).
  See `evaluation.py` and the workflow in the README / `ROADMAP.md`.
