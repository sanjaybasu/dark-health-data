"""End-to-end orchestration: discover -> fetch -> extract -> validate -> curate -> publish.

The same flow runs for any dataset; the connector and extractor are the only
moving parts. Invoke via the ``dhd`` console script or ``python -m dark_health_data``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from . import pdf
from .config import settings
from .curate import curate
from .discovery import discover
from .extract import get_extractor
from .fetch import fetch
from .models import SourceDocument, sha256_text
from .publish import write_croissant, write_data_dictionary, write_dataset_card
from .registry import load_datasets
from .verify import verify_records


def _dataset_meta(dataset_id: str) -> dict[str, Any]:
    for d in load_datasets():
        if d.get("id") == dataset_id:
            return d
    return {"id": dataset_id, "name": dataset_id}


def run_dataset(
    dataset_id: str,
    *,
    extractor_name: str = "rule",
    second_extractor_name: str | None = None,
    limit: int | None = None,
    ocr: bool = False,
    write_parquet: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline for one dataset; returns a run summary.

    If ``second_extractor_name`` is set, that extractor runs as a decorrelated
    second expert and its records feed the ensemble verifier.
    """
    extractor = get_extractor(extractor_name)
    second = get_extractor(second_extractor_name) if second_extractor_name else None
    candidates = discover(dataset_id)
    if limit:
        candidates = candidates[:limit]

    def log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    log(f"[discover] {len(candidates)} candidate document(s) for '{dataset_id}'")

    records = []
    second_records = [] if second else None
    docs: list[SourceDocument] = []
    doc_texts: dict[str, str] = {}
    from .connectors import get_connector

    connector = get_connector(dataset_id)

    failed = 0
    for cand in candidates:
        log(f"[fetch]    {cand.jurisdiction or '?'} {cand.report_year or ''} <- {cand.location}")
        try:
            doc = fetch(cand)
            text = pdf.extract_text(doc.local_path, ocr=ocr)
            doc.n_pages = text.count("[[PAGE ")
            doc.content_sha256 = sha256_text(text)
            doc_texts[doc.document_id] = text
            recs = extractor.extract(text, doc, connector)
            log(f"[extract]  {len(recs)} record(s) via '{extractor.name}' from {doc.n_pages} page(s)")
            records.extend(recs)
            if second is not None:
                second_records.extend(second.extract(text, doc, connector))
            docs.append(doc)
        except Exception as exc:  # one bad document must not sink a multi-document run
            failed += 1
            log(f"[error]    skipped {cand.jurisdiction or cand.location}: {type(exc).__name__}: {exc}")

    ens_kwargs: dict[str, Any] = {}
    if second_records is not None:
        log(f"[ensemble] {len(second_records)} record(s) via 2nd extractor '{second.name}'")
        ens_kwargs = dict(
            second_records=second_records,
            ensemble_key_fn=connector.ensemble_key,
            ensemble_fields=connector.ensemble_fields,
        )

    report = verify_records(records, connector=connector, doc_texts=doc_texts, **ens_kwargs)
    log(
        f"[verify]   {report['n_records']} records — "
        f"pass={report['qa_pass']} warn={report['qa_warn']} fail={report['qa_fail']} | "
        f"mean_trust={report['mean_trust']} review={report['review_recommended']}"
    )

    out_dir = settings.processed_dir / dataset_id
    curated = curate(records, docs, out_dir, write_parquet=write_parquet)

    meta = _dataset_meta(dataset_id)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)
    log(f"[publish]  wrote tables + DATA_DICTIONARY.md, DATASET_CARD.md, croissant.json to {out_dir}")

    return {"dataset_id": dataset_id, "validation": report, "curation": curated,
            "documents_failed": failed, "out_dir": str(out_dir)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_list(_: argparse.Namespace) -> int:
    datasets = load_datasets()
    print(f"{len(datasets)} dataset family(ies) in the registry:\n")
    for d in datasets:
        status = d.get("status", "planned")
        print(f"  {d.get('id'):14s} [{status:9s}] {d.get('name')}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    summary = run_dataset(
        args.dataset,
        extractor_name=args.extractor,
        second_extractor_name=args.second_extractor,
        limit=args.limit,
        ocr=args.ocr,
        write_parquet=not args.no_parquet,
    )
    print("\nDone:", summary["out_dir"])
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .evaluation import write_sample

    info = write_sample(args.dataset, args.n, Path(args.out), stratify=args.stratify, seed=args.seed)
    print(f"Wrote {info['sampled']} rows (stratified by {info['stratify']}) to {info['out']}")
    print("Fill the `correct` column (1/0) by checking each row against its source, then run `dhd evaluate`.")
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .evaluation import evaluate

    r = evaluate(args.dataset, Path(args.gold), alpha=args.alpha, delta=args.delta, stratify=args.stratify)
    ci = r.get("overall_accuracy_ci95")
    ci_s = f" [95% CI {ci[0]}–{ci[1]}]" if ci else ""
    print(f"labeled={r['n_labeled']}  accuracy={r['overall_accuracy']}{ci_s}  "
          f"(unmatched gold rows: {r['n_unmatched_gold']})")
    print(f"conformal @ alpha={r['alpha']}: trust>={r['conformal_threshold']} -> "
          f"coverage={r['coverage_at_alpha']}, accepted_error={r['accepted_error']}")
    print(f"full report: {r['report_path']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dhd", description="Dark Health Data pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list dataset families in the registry")
    p_list.set_defaults(func=_cmd_list)

    p_run = sub.add_parser("run", help="run the pipeline for a dataset")
    p_run.add_argument("--dataset", required=True, help="dataset id, e.g. 'eqr'")
    p_run.add_argument("--extractor", default="rule", choices=["rule", "llm", "vlm"])
    p_run.add_argument("--second-extractor", default=None, choices=["rule", "llm", "vlm"],
                       help="decorrelated 2nd extractor for the ensemble verifier (e.g. 'vlm' for local Qwen)")
    p_run.add_argument("--limit", type=int, default=None, help="max documents to process")
    p_run.add_argument("--ocr", action="store_true", help="OCR pages with no text layer")
    p_run.add_argument("--no-parquet", action="store_true", help="skip parquet output")
    p_run.set_defaults(func=_cmd_run)

    p_sample = sub.add_parser("sample", help="draw a stratified gold sample to label")
    p_sample.add_argument("--dataset", required=True)
    p_sample.add_argument("--n", type=int, default=100, help="sample size")
    p_sample.add_argument("--stratify", default=None, help="column to stratify by (default: first identity column)")
    p_sample.add_argument("-o", "--out", default="gold_sample.csv", help="output CSV path")
    p_sample.add_argument("--seed", type=int, default=0)
    p_sample.set_defaults(func=_cmd_sample)

    p_eval = sub.add_parser("evaluate", help="score a filled gold sample + calibrate the conformal gate")
    p_eval.add_argument("--dataset", required=True)
    p_eval.add_argument("--gold", required=True, help="gold CSV with the `correct` column filled")
    p_eval.add_argument("--alpha", type=float, default=0.05, help="target max error among auto-accepted")
    p_eval.add_argument("--delta", type=float, default=0.05, help="confidence level for the bound")
    p_eval.add_argument("--stratify", default=None, help="column for per-stratum calibration")
    p_eval.set_defaults(func=_cmd_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
