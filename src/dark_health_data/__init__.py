"""Hidden Health Data — turning buried public-health documents into open, research-ready datasets."""

from __future__ import annotations

from .config import __version__, settings
from .pipeline import run_dataset

__all__ = ["__version__", "settings", "run_dataset"]
