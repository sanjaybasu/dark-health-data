"""Load the dataset registry and per-dataset source seeds.

``registry/datasets.yaml`` is the structured catalog of buried dataset families
(the literature review, made machine-readable). ``registry/sources_<id>.yaml``
lists concrete documents/source pages for a dataset's connector to discover.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import settings


def load_datasets(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or settings.registry_dir / "datasets.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("datasets", [])


def load_sources(dataset_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Load source entries for a dataset.

    Reads every ``registry/sources_<id>*.yaml`` file (so a user can drop in a
    ``sources_eqr_live.yaml`` alongside the shipped fixtures without editing it).
    ``.example`` files are ignored.
    """
    if path is not None:
        files = [path]
    else:
        files = sorted(settings.registry_dir.glob(f"sources_{dataset_id}*.yaml"))
    sources: list[dict[str, Any]] = []
    for f in files:
        if f.suffix != ".yaml":  # skip e.g. .example
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        sources.extend(data.get("sources", []))
    return sources
