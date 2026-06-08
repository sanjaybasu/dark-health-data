#!/usr/bin/env python3
"""Package a processed dataset into a deposit-ready zip (Zenodo / Data in Brief).

Bundles the curated tables + lossless records + generated docs + a manifest (with
per-file sha256, sizes, and row counts) into dist/<dataset>-v<version>.zip.

    python scripts/make_release.py --dataset eqr

Stdlib only. Run after `dhd run --dataset <id> ...`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dark_health_data.config import settings  # noqa: E402
from dark_health_data.release import package_dataset  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Package a processed dataset for deposit")
    ap.add_argument("--dataset", required=True, help="dataset id, e.g. 'eqr'")
    ap.add_argument("--out", default="dist", help="output directory")
    args = ap.parse_args(argv)

    if not (settings.processed_dir / args.dataset).exists():
        print(f"No processed data at {settings.processed_dir / args.dataset}. "
              f"Run `dhd run --dataset {args.dataset}` first.")
        return 1

    zip_path = package_dataset(args.dataset, args.out)
    print(f"wrote {zip_path} ({zip_path.stat().st_size / 1024:.0f} KB)")
    print("\nUpload the zip to Zenodo (CC0) and cite its DOI in the Data in Brief paper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
