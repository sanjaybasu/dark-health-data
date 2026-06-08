"""Test isolation for the data directory.

The offline suite exercises the real pipeline (run_dataset -> curate -> publish),
which writes to ``settings.processed_dir``. Without isolation that is the live
``data/processed/`` holding real extracted datasets, so a test run silently clobbers
them with the synthetic demo output. This autouse fixture points ``data_dir`` (and
thus raw/cache/processed/sample) at a per-test tmp directory. ``repo_root`` and
``registry_dir`` stay real, so committed fixtures (data/sample/*.txt) and the
registry still load.
"""
from __future__ import annotations

import pytest

from dark_health_data.config import settings


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
