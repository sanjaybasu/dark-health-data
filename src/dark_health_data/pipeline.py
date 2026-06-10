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


# ---------------------------------------------------------------------------
# Incremental merge: expansion only ever ADDS coverage.
#
# A wave runs only NEW sources and unions the result with the already-published
# canonical dataset (the dist/<id>-v*.zip), keyed by document source URL. A doc
# present in the new run REPLACES its prior version; every other prior document
# is retained untouched. So a flaky fetch this run can only affect the new
# additions -- it can never regress data we already shipped -- and we never
# re-extract (re-pay for) documents already in the canonical set.
# ---------------------------------------------------------------------------


def _record_classmap(connector) -> dict[str, Any]:
    """record_type string -> record model class, for rehydrating serialized rows."""
    return {cls.model_fields["record_type"].default: cls for cls in connector.record_models}


def _select_canonical_zip(dataset_id: str):
    """Newest published dist/<id>-v<M>.<m>.<p>.zip by SEMANTIC version (not lexical:
    v0.10.0 > v0.9.0), ties broken by mtime. Returns a Path or None if none exist."""
    import re

    dist = settings.repo_root / "dist"
    if not dist.exists():
        return None
    ranked = []
    for p in dist.glob(f"{dataset_id}-v*.zip"):
        m = re.search(rf"{re.escape(dataset_id)}-v(\d+)\.(\d+)\.(\d+)", p.name)
        version = tuple(int(x) for x in m.groups()) if m else (-1, -1, -1)
        ranked.append((version, p.stat().st_mtime, p))
    if not ranked:
        return None
    ranked.sort()
    return ranked[-1][2]


def _load_canonical(dataset_id: str, connector) -> tuple[list, list[SourceDocument]]:
    """Rehydrate (records, documents) from the newest published dist zip.

    Returns ([], []) ONLY when no prior release exists (a legitimate first run). If a
    zip IS present but can't be read, is missing its tables, yields zero records, or
    holds an unknown record_type, we RAISE rather than return an empty canonical --
    a silent empty would let the merge republish only the new wave, which is exactly
    the regression this design exists to prevent.
    """
    import csv
    import io
    import json
    import zipfile

    zp = _select_canonical_zip(dataset_id)
    if zp is None:
        return [], []
    try:
        zf = zipfile.ZipFile(zp)
        names = zf.namelist()
        rj = next((n for n in names if n.endswith("records.jsonl")), None)
        dc = next((n for n in names if n.endswith("documents.csv")), None)
        records_text = zf.read(rj).decode("utf-8") if rj else None
        docs_text = zf.read(dc).decode("utf-8") if dc else None
    except Exception as exc:
        raise RuntimeError(f"cannot read canonical zip {zp}: {type(exc).__name__}: {exc}") from exc
    if records_text is None or docs_text is None:
        raise RuntimeError(f"{zp.name} is missing records.jsonl and/or documents.csv")

    classmap = _record_classmap(connector)
    records = []
    for line in records_text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rt = row.get("record_type")
        cls = classmap.get(rt)
        if cls is None:
            raise RuntimeError(
                f"{zp.name}: record_type {rt!r} is not in the '{dataset_id}' connector's "
                f"record_models -- refusing to silently drop canonical records")
        records.append(cls.model_validate(row))
    if not records:
        raise RuntimeError(f"{zp.name} contains zero records -- refusing to merge against it")
    docs = [
        SourceDocument.model_validate({k: (v if v != "" else None) for k, v in row.items()})
        for row in csv.DictReader(io.StringIO(docs_text))
    ]
    return records, docs


def _merge_records_docs(existing_records, existing_docs, new_records, new_docs):
    """Union by stable document id -- the sha256 of the document bytes, which is
    always present and equals each record's ``provenance.source_document_id``. A
    document re-extracted this wave REPLACES its prior records; every other prior
    document is kept untouched. Keying on the content hash (not ``source_url``) means
    URL-less documents and URL variants (trailing slash / query string) dedup
    correctly. Pure function (no I/O) so it is unit-testable."""
    new_ids = {d.document_id for d in new_docs}
    kept_records = [r for r in existing_records
                    if r.provenance.source_document_id not in new_ids]
    kept_docs = [d for d in existing_docs if d.document_id not in new_ids]
    return kept_records + new_records, kept_docs + new_docs


def _aggregate_report(records) -> dict[str, Any]:
    """Recompute the summary over a (possibly merged) record set without re-verifying:
    canonical records keep their stored trust/QA, new records were just verified."""
    from .models import QAStatus

    trusts = [r.trust_score for r in records if r.trust_score is not None]
    return {
        "n_records": len(records),
        "qa_pass": sum(1 for r in records if r.qa_status == QAStatus.PASS),
        "qa_warn": sum(1 for r in records if r.qa_status == QAStatus.WARN),
        "qa_fail": sum(1 for r in records if r.qa_status == QAStatus.FAIL),
        "mean_trust": round(sum(trusts) / len(trusts), 4) if trusts else None,
        "review_recommended": sum(1 for r in records if r.review_recommended),
    }


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
    merge: bool = False,
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

    With ``merge=True`` the run is treated as an *expansion*: the freshly extracted
    records are unioned (by document source URL) with the already-published canonical
    dataset before curation, so it only ever adds coverage and never regresses or
    re-extracts prior documents. Point the source registry at NEW documents only.
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
            if doc.document_id in docs_by_id:
                # Distinct source URLs can resolve to the SAME PDF (e.g. one health
                # system's CHNA listed under several member hospitals). They share a
                # content-hash document_id, so chunking both yields duplicate batch
                # custom_ids (a hard 400). Extract each unique document once.
                log(f"[dedup]    {cand.jurisdiction or cand.location}: same content as an "
                    f"already-fetched document -- skipping duplicate URL")
                continue
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
    log(f"[verify]   {report['n_records']} new records — pass={report['qa_pass']} "
        f"warn={report['qa_warn']} fail={report['qa_fail']} | mean_trust={report['mean_trust']}")

    if merge:
        ex_records, ex_docs = _load_canonical(dataset_id, connector)
        n_new_docs = len(docs)
        records, docs = _merge_records_docs(ex_records, ex_docs, records, docs)
        replaced_docs = len(ex_docs) - (len(docs) - n_new_docs)
        report = _aggregate_report(records)
        log(f"[merge]    canonical {len(ex_docs)} doc(s) + {n_new_docs} new "
            f"(replaced {replaced_docs}) -> {report['n_records']} records, "
            f"{len(docs)} document(s)")

    out_dir = settings.processed_dir / dataset_id
    curated = curate(records, docs, out_dir, write_parquet=write_parquet)
    meta = _dataset_meta(dataset_id)
    write_data_dictionary(out_dir, connector.record_models)
    write_dataset_card(out_dir, meta, {**curated, **report})
    write_croissant(out_dir, meta, curated)
    if merge:
        # Update the canonical dist zip atomically with processed/, so the NEXT
        # --merge wave chains off this output instead of a stale prior release.
        from .release import package_dataset
        zp = package_dataset(dataset_id, settings.repo_root / "dist")
        log(f"[publish]  re-packaged canonical {zp.name} so the next --merge wave chains off it")
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
        merge=args.merge,
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
    p_batch.add_argument("--merge", action="store_true",
                         help="expansion mode: union new records with the published canonical "
                              "dataset by document URL (adds coverage, never regresses/re-extracts)")
    p_batch.add_argument("--no-parquet", action="store_true", help="skip parquet output")
    p_batch.add_argument("--poll-seconds", type=int, default=60, help="seconds between batch status polls")
    p_batch.add_argument("--max-wait", type=int, default=86400, help="max seconds to wait before deferring")
    p_batch.set_defaults(func=_cmd_batch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
