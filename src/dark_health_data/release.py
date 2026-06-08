"""Package a processed dataset into a deposit-ready zip (Zenodo / Data in Brief).

The dist/<dataset>-v<version>.zip is the *canonical* published artifact: it is what
``run_dataset_batch(merge=True)`` reads back as the prior dataset and unions against.
So packaging must be a callable the pipeline can invoke inline (to keep dist in sync
with data/processed after every merge wave), not only a manual script step. Stdlib
only, so the dependency-light core is preserved.
"""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from datetime import date
from pathlib import Path

from .config import __version__, settings


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(path: Path) -> int | None:
    if path.suffix != ".csv":
        return None
    with path.open(encoding="utf-8", newline="") as fh:
        # logical CSV records (handles fields with embedded newlines)
        return max(0, sum(1 for _ in csv.reader(fh)) - 1)


def package_dataset(dataset_id: str, out_dir: Path | str = "dist") -> Path:
    """Zip data/processed/<dataset_id> into <out_dir>/<dataset_id>-v<version>.zip.

    Returns the zip path. Raises FileNotFoundError if the processed dir is missing.
    """
    src = settings.processed_dir / dataset_id
    if not src.exists():
        raise FileNotFoundError(
            f"No processed data at {src}. Run the pipeline for '{dataset_id}' first."
        )

    # Exclude any MANIFEST.json already in the dir (it is a packaging output, not an
    # input) so re-packaging never double-writes the manifest entry.
    files = sorted(p for p in src.iterdir() if p.is_file() and p.name != "MANIFEST.json")
    manifest = {
        "dataset": dataset_id,
        "software_version": __version__,
        "packaged": date.today().isoformat(),
        "license": "CC0-1.0 (data); Apache-2.0 (code)",
        "files": [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p), "rows": _rows(p)}
            for p in files
        ],
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"{dataset_id}-v{__version__}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=f"{dataset_id}/{p.name}")
        z.writestr(f"{dataset_id}/MANIFEST.json", json.dumps(manifest, indent=2))
    return zip_path
