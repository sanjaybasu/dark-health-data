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


def run_dataset_batch(
    dataset_id: str,
    *,
    model: str | None = None,
    limit: int | None = None,
    ocr: bool = False,
    poll_seconds: int = 60,
    max_wait: int = 86400,
    write_parquet: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run a dataset through the Message Batches API (~50% cheaper, latency-tolerant).

    Fetches + extracts text for every candidate, submits all chunks as one (or a few)
    batches, polls to completion, then maps results back and runs the same
    verify -> curate -> publish tail as ``run_dataset``. A manifest in
    ``data/cache/batches/<id>.json`` records the batch ids so re-running resumes
    (re-polls + collects) without resubmitting -- no re-billing of succeeded chunks.
    Failed requests (errored/expired/canceled) are unbilled and reported as dropped.
    """
    import json
    import time

    from .connectors import get_connector
    from .extract.llm import BatchLLMExtractor

    def log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    connector = get_connector(dataset_id)
    extractor = BatchLLMExtractor(model=model)

    candidates = discover(dataset_id)
    if limit:
        candidates = candidates[:limit]

    items: list[tuple[SourceDocument, str]] = []
    docs: list[SourceDocument] = []
    docs_by_id: dict[str, SourceDocument] = {}
    doc_texts: dict[str, str] = {}
    for cand in candidates:
        try:
            doc = fetch(cand)
            text = pdf.extract_text(doc.local_path, ocr=ocr)
            doc.n_pages = text.count("[[PAGE ")
            doc.content_sha256 = sha256_text(text)
            items.append((doc, text))
            docs.append(doc)
            docs_by_id[doc.document_id] = doc
            doc_texts[doc.document_id] = text
        except Exception as exc:
            log(f"[error]    skipped {cand.jurisdiction or cand.location}: {type(exc).__name__}: {exc}")

    requests, id_map = extractor.build_requests(items, connector)
    route = {cid: docs_by_id[did] for cid, (did, _i) in id_map.items()}
    log(f"[batch]    {len(items)} document(s) -> {len(requests)} chunk request(s)")
    if not requests:
        return {"dataset_id": dataset_id, "status": "no-requests"}

    client = extractor._client()
    bdir = settings.cache_dir / "batches"
    bdir.mkdir(parents=True, exist_ok=True)
    manifest = bdir / f"{dataset_id}.json"

    batch_ids: list[str] = []
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        if m.get("request_count") == len(requests) and m.get("batch_ids"):
            batch_ids = m["batch_ids"]
            log(f"[batch]    resuming {len(batch_ids)} batch(es) from manifest (no resubmit)")
    if not batch_ids:
        block = 90000  # stay under the 100k-requests / 256MB per-batch ceilings
        for i in range(0, len(requests), block):
            bid = extractor.submit(client, requests[i:i + block])
            batch_ids.append(bid)
            log(f"[batch]    submitted {bid} ({len(requests[i:i + block])} requests)")
        manifest.write_text(json.dumps(
            {"dataset": dataset_id, "model": extractor.model, "request_count": len(requests),
             "batch_ids": batch_ids}, indent=2), encoding="utf-8")

    waited, pending = 0, set(batch_ids)
    while pending and waited <= max_wait:
        for bid in list(pending):
            if extractor.poll(client, bid) == "ended":
                pending.discard(bid)
                log(f"[batch]    {bid} ended")
        if pending:
            time.sleep(poll_seconds)
            waited += poll_seconds
    if pending:
        log(f"[batch]    still processing after {max_wait}s; re-run `dhd batch --dataset "
            f"{dataset_id}` later to resume (manifest kept).")
        return {"dataset_id": dataset_id, "status": "pending", "batch_ids": batch_ids}

    records, dropped = [], []
    for bid in batch_ids:
        r, d = extractor.collect(client, bid, route, connector)
        records.extend(r)
        dropped.extend(d)
    log(f"[batch]    collected {len(records)} record(s); {len(dropped)} request(s) failed "
        f"(unbilled errored/expired/canceled)")

    report = verify_records(records, connector=connector, doc_texts=doc_texts)
    log(f"[verify]   {report['n_records']} records — pass={report['qa_pass']} "
        f"warn={report['qa_warn']} fail={report['qa_fail']} | mean_trust={report['mean_trust']}")
    out_dir = settings.processed_dir / dataset_id
    curated = curate(records, docs, out_dir, write_parquet=write_parquet)
    meta = _dataset_meta(dataset_id)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)
    manifest.unlink(missing_ok=True)  # success: clear so a fresh run re-submits
    log(f"[publish]  wrote tables + metadata to {out_dir}")
    return {"dataset_id": dataset_id, "validation": report, "curation": curated,
            "dropped": len(dropped), "out_dir": str(out_dir)}


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


def _cmd_agreement(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .evaluation import agreement

    r = agreement(Path(args.a), Path(args.b))
    if r.get("n_overlap", 0) == 0:
        print("No row_uids were labelled by both reviewers — nothing to compare.")
        return 1
    c = r["contingency"]
    print(f"overlap (rows labelled by both): {r['n_overlap']}")
    print(f"percent agreement: {r['percent_agreement']}")
    print(f"Cohen's kappa:     {r['cohen_kappa']}")
    print(f"Gwet's AC1:        {r['gwet_ac1']}  (more stable at high agreement)")
    print(f"  both correct={c['both_correct']}  both incorrect={c['both_incorrect']}  "
          f"A+/B-={c['A_correct_B_incorrect']}  A-/B+={c['A_incorrect_B_correct']}")
    d = r["disagreement_row_uids"]
    print(f"disagreements to adjudicate ({len(d)}): {', '.join(d) if d else 'none'}")
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    summary = run_dataset_batch(
        args.dataset,
        limit=args.limit,
        ocr=args.ocr,
        write_parquet=not args.no_parquet,
        poll_seconds=args.poll_seconds,
        max_wait=args.max_wait,
    )
    status = summary.get("status")
    if status == "pending":
        print(f"\nBatch(es) still processing: {summary['batch_ids']}. Re-run to resume.")
    else:
        print("\nDone:", summary.get("out_dir", status))
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

    p_agree = sub.add_parser(
        "agreement",
        help="inter-rater agreement (Cohen's kappa, Gwet's AC1) on two reviewers' overlap",
    )
    p_agree.add_argument("--a", required=True, help="first reviewer's filled CSV (row_uid + correct)")
    p_agree.add_argument("--b", required=True, help="second reviewer's filled CSV (row_uid + correct)")
    p_agree.set_defaults(func=_cmd_agreement)

    p_batch = sub.add_parser(
        "batch",
        help="run a dataset via the Message Batches API (~50%% cheaper, async bulk; resumable)",
    )
    p_batch.add_argument("--dataset", required=True, help="dataset id, e.g. 'chna'")
    p_batch.add_argument("--limit", type=int, default=None, help="max documents to process")
    p_batch.add_argument("--ocr", action="store_true", help="OCR pages with no text layer")
    p_batch.add_argument("--no-parquet", action="store_true", help="skip parquet output")
    p_batch.add_argument("--poll-seconds", type=int, default=60, help="seconds between batch status polls")
    p_batch.add_argument("--max-wait", type=int, default=86400, help="max seconds to wait before deferring")
    p_batch.set_defaults(func=_cmd_batch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
