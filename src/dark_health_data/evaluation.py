"""Gold-set sampling and evaluation -- closes the verification loop.

Workflow (the data-resource paper's validation sub-study, push-button):

    dhd run      --dataset eqr --extractor llm        # produce predictions
    dhd sample   --dataset eqr --n 100 --stratify state -o gold/eqr.csv
    # ... a human fills the `correct` column (1/0) by checking each row's source ...
    dhd evaluate --dataset eqr --gold gold/eqr.csv --alpha 0.05 --stratify state

``evaluate`` reports per-row accuracy, a risk-coverage curve, and calibrates the
conformal gate (overall and per stratum) so you can state: "auto-accepting rows with
trust >= T yields <= alpha error with coverage C." Gold is keyed on identity columns,
so labels survive re-extraction.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from .config import settings
from .connectors import get_connector
from .models import RECORD_TABLE
from .verify.conformal import _threshold

_TRUE = {"1", "true", "yes", "y", "correct", "t"}
_FALSE = {"0", "false", "no", "n", "incorrect", "f"}


def primary_table(connector) -> str:
    rt = connector.record_models[0].model_fields["record_type"].default
    return RECORD_TABLE[rt]


def row_uid(row: dict[str, Any], id_cols: list[str]) -> str:
    raw = "|".join(str(row.get(c, "")).strip().lower() for c in id_cols)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_predictions(dataset_id: str) -> tuple[Any, list[dict[str, Any]]]:
    connector = get_connector(dataset_id)
    path = settings.processed_dir / dataset_id / f"{primary_table(connector)}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No predictions at {path}. Run `dhd run --dataset {dataset_id}` first."
        )
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return connector, rows


def stratified_sample(
    rows: list[dict[str, Any]], n: int, stratify_key: Optional[str] = None, seed: int = 0
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    if n >= len(rows):
        return list(rows)
    if not stratify_key:
        return rng.sample(rows, n)
    strata: dict[Any, list] = defaultdict(list)
    for r in rows:
        strata[r.get(stratify_key, "")].append(r)
    total = len(rows)
    picked: list[dict[str, Any]] = []
    for group in strata.values():
        share = min(len(group), max(1, round(n * len(group) / total)))
        picked.extend(rng.sample(group, share))
    rng.shuffle(picked)
    return picked[:n]


def write_sample(
    dataset_id: str, n: int, out_path: Path, stratify: Optional[str] = None, seed: int = 0
) -> dict[str, Any]:
    connector, rows = load_predictions(dataset_id)
    id_cols = connector.identity_columns
    stratify = stratify or (id_cols[0] if id_cols else None)
    sample = stratified_sample(rows, n, stratify, seed)

    cols = (
        ["row_uid"] + id_cols + connector.ensemble_fields
        + ["trust_score", "review_recommended", "prov_source_url", "prov_page_start", "correct"]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sample:
            out = {c: r.get(c, "") for c in cols}
            out["row_uid"] = row_uid(r, id_cols)
            out["correct"] = ""  # human fills 1/0
            w.writerow(out)
    return {"dataset": dataset_id, "sampled": len(sample), "stratify": stratify, "out": str(out_path)}


def _parse_correct(value: str) -> Optional[bool]:
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def risk_coverage(pairs: list[tuple[float, bool]]) -> list[dict[str, float]]:
    """Accuracy among the top-trust fraction, swept over coverage."""
    ordered = sorted(pairs, key=lambda p: p[0], reverse=True)
    n = len(ordered)
    curve, correct = [], 0
    for i, (_, ok) in enumerate(ordered, start=1):
        correct += 1 if ok else 0
        curve.append({"coverage": round(i / n, 3), "accuracy": round(correct / i, 4),
                      "trust_threshold": round(ordered[i - 1][0], 4)})
    return curve


def wilson_ci(k: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson 95% confidence interval for a proportion k/n (better than normal at extremes)."""
    if n == 0:
        return [0.0, 1.0]
    import math

    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def evaluate(
    dataset_id: str, gold_path: Path, alpha: float = 0.05, delta: float = 0.05,
    stratify: Optional[str] = None,
) -> dict[str, Any]:
    connector, preds = load_predictions(dataset_id)
    id_cols = connector.identity_columns
    stratify = stratify or (id_cols[0] if id_cols else None)
    pred_by_uid = {row_uid(r, id_cols): r for r in preds}

    with gold_path.open(encoding="utf-8") as fh:
        gold = list(csv.DictReader(fh))

    pairs: list[tuple[float, bool]] = []
    strata_pairs: dict[Any, list[tuple[float, bool]]] = defaultdict(list)
    matched = unmatched = 0
    for g in gold:
        correct = _parse_correct(g.get("correct", ""))
        if correct is None:
            continue
        pred = pred_by_uid.get(g.get("row_uid", ""))
        if pred is None:
            unmatched += 1
            continue
        matched += 1
        try:
            score = float(pred.get("trust_score") or 0.0)
        except ValueError:
            score = 0.0
        pairs.append((score, correct))
        strata_pairs[pred.get(stratify, "")].append((score, correct))

    n = len(pairs)
    n_correct = sum(1 for _, c in pairs if c)
    accuracy = n_correct / n if n else None
    tau = _threshold(pairs, alpha, delta)
    accepted = [c for s, c in pairs if s >= tau]
    report = {
        "dataset": dataset_id,
        "n_labeled": n,
        "n_unmatched_gold": unmatched,
        "overall_accuracy": round(accuracy, 4) if accuracy is not None else None,
        "overall_accuracy_ci95": wilson_ci(n_correct, n) if n else None,
        "alpha": alpha,
        "delta": delta,
        "conformal_threshold": None if tau == float("inf") else round(tau, 4),
        "coverage_at_alpha": round(len(accepted) / n, 4) if n else 0.0,
        "accepted_error": round(sum(1 for c in accepted if not c) / len(accepted), 4) if accepted else None,
        "stratified_thresholds": {
            str(k): (None if (t := _threshold(v, alpha, delta)) == float("inf") else round(t, 4))
            for k, v in strata_pairs.items()
        },
        "risk_coverage": risk_coverage(pairs),
    }
    out = settings.processed_dir / dataset_id / "evaluation_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(out)
    return report
