"""Configuration and filesystem layout.

Paths are resolved relative to the repository root so the pipeline behaves the
same whether invoked via the console script, ``python -m``, or the demo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# repo root = three parents up from this file (src/dark_health_data/config.py)
REPO_ROOT = Path(__file__).resolve().parents[2]

__version__ = "0.3.0"


@dataclass
class Settings:
    repo_root: Path = REPO_ROOT
    registry_dir: Path = field(default_factory=lambda: REPO_ROOT / "registry")
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def sample_dir(self) -> Path:
        return self.data_dir / "sample"

    # --- LLM extraction (optional) ---
    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def llm_model(self) -> str:
        # Sonnet is a good default for structured extraction quality/cost.
        # Override with OHD_LLM_MODEL (e.g. claude-haiku-4-5-20251001 for cheaper runs).
        return os.environ.get("OHD_LLM_MODEL", "claude-sonnet-4-6")

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.cache_dir, self.processed_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
