"""Connector registry.

To add a new buried dataset, implement a Connector subclass and register it here.
"""

from __future__ import annotations

from .base import CandidateDoc, Connector
from .chna import CHNAConnector
from .eqr import EQRConnector
from .mmrc import MMRCConnector
from .nursing_home_2567 import NursingHome2567Connector
from .waiver_1115 import Waiver1115Connector

CONNECTORS: dict[str, Connector] = {
    EQRConnector.dataset_id: EQRConnector(),
    CHNAConnector.dataset_id: CHNAConnector(),
    MMRCConnector.dataset_id: MMRCConnector(),
    Waiver1115Connector.dataset_id: Waiver1115Connector(),
    NursingHome2567Connector.dataset_id: NursingHome2567Connector(),
}


def get_connector(dataset_id: str) -> Connector:
    try:
        return CONNECTORS[dataset_id]
    except KeyError as exc:
        known = ", ".join(sorted(CONNECTORS)) or "(none)"
        raise KeyError(f"No connector for dataset '{dataset_id}'. Known: {known}") from exc


__all__ = ["CandidateDoc", "Connector", "CONNECTORS", "get_connector"]
