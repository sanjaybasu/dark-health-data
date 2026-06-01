"""Connector registry.

To add a new buried dataset, implement a Connector subclass and register it here.
"""

from __future__ import annotations

from .base import CandidateDoc, Connector
from .chna import CHNAConnector
from .eqr import EQRConnector
from .mmrc import MMRCConnector

CONNECTORS: dict[str, Connector] = {
    EQRConnector.dataset_id: EQRConnector(),
    CHNAConnector.dataset_id: CHNAConnector(),
    MMRCConnector.dataset_id: MMRCConnector(),
}


def get_connector(dataset_id: str) -> Connector:
    try:
        return CONNECTORS[dataset_id]
    except KeyError as exc:
        known = ", ".join(sorted(CONNECTORS)) or "(none)"
        raise KeyError(f"No connector for dataset '{dataset_id}'. Known: {known}") from exc


__all__ = ["CandidateDoc", "Connector", "CONNECTORS", "get_connector"]
