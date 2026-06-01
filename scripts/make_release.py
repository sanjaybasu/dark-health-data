#!/usr/bin/env python3
"""Package a processed dataset into a deposit-ready zip (Zenodo / Data in Brief).

Bundles the curated tables + lossless records + generated docs + a manifest (with
per-file sha256, sizes, and row counts) into dist/<dataset>-v<version>.zip.

    python scripts/make_release.py --dataset eqr

Stdlib only. Run after `dhd run --dataset <id> ...`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data import __version__  # noqa: E402
from dark_health_data.config import settings  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(path: Path) -> int | None:
    if path.suffix != ".csv":
        return None
    import csv
    with path.open(encoding="utf-8", newline="") as fh:
        # count logical CSV records (handles fields containing embedded newlines)
        return max(0, sum(1 for _ in csv.reader(fh)) - 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Package a processed dataset for deposit")
    ap.add_argument("--dataset", required=True, help="dataset id, e.g. 'eqr'")
    ap.add_argument("--out", default="dist", help="output directory")
    args = ap.parse_args(argv)

    src = settings.processed_dir / args.dataset
    if not src.exists():
        print(f"No processed data at {src}. Run `dhd run --dataset {args.dataset}` first.")
        return 1

    files = sorted(p for p in src.iterdir() if p.is_file())
    manifest = {
        "dataset": args.dataset,
        "software_version": __version__,
        "packaged": date.today().isoformat(),
        "license": "CC0-1.0 (data); Apache-2.0 (code)",
        "files": [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p), "rows": _rows(p)}
            for p in files
        ],
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{args.dataset}-v{__version__}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=f"{args.dataset}/{p.name}")
        z.writestr(f"{args.dataset}/MANIFEST.json", json.dumps(manifest, indent=2))

    print(f"wrote {zip_path} ({zip_path.stat().st_size/1024:.0f} KB, {len(files)} files)")
    for f in manifest["files"]:
        r = f" — {f['rows']} rows" if f["rows"] is not None else ""
        print(f"   {f['name']} ({f['bytes']} bytes){r}")
    print("\nUpload the zip to Zenodo (CC0) and cite its DOI in the Data in Brief paper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
