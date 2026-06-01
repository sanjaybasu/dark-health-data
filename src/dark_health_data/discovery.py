"""Discovery: registry source entries -> concrete candidate documents."""

from __future__ import annotations

from .connectors import get_connector
from .connectors.base import CandidateDoc
from .registry import load_sources


def discover(dataset_id: str) -> list[CandidateDoc]:
    connector = get_connector(dataset_id)
    candidates: list[CandidateDoc] = []
    for entry in load_sources(dataset_id):
        candidates.extend(connector.discover(entry))
    return candidates
