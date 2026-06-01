"""Connector abstraction.

A *connector* encapsulates everything dataset-specific: where the documents
live, what structured records to pull out, the schema/prompt the LLM extractor
should use, and a deterministic reference parser for offline/smoke runs.

The generic pipeline (discover -> fetch -> extract -> validate -> curate ->
publish) is identical across datasets; only the connector changes. Adding a new
buried dataset (CHNAs, MMRC reports, 1115 waivers, ...) means writing one new
connector, not touching the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel

from ..models import ExtractionRecord, SourceDocument


class CandidateDoc(BaseModel):
    """A document we intend to fetch and process, produced by discovery."""

    dataset_id: str
    url: Optional[str] = None  # remote source
    local_path: Optional[str] = None  # local fixture / already-downloaded file
    title: Optional[str] = None
    publisher: Optional[str] = None
    jurisdiction: Optional[str] = None  # state/territory
    program: Optional[str] = None
    report_year: Optional[int] = None

    @property
    def location(self) -> str:
        return self.url or self.local_path or "<unknown>"


class Connector(ABC):
    """Base class for all dataset connectors."""

    #: short stable id, e.g. "eqr"
    dataset_id: str = ""
    #: human-readable name
    name: str = ""
    #: one-line description
    description: str = ""
    #: version of this connector's extraction logic (recorded in provenance)
    version: str = "0.1.0"
    #: the ExtractionRecord subclasses this connector emits (used to generate the
    #: data dictionary). Set per connector.
    record_models: list[type] = []

    def constraints(self) -> list:
        """Declarative domain axioms for the verification layer.

        Returns a list of ``dark_health_data.verify.Constraint`` objects. Override
        per connector; default is none. These are consumed by both the symbolic
        verifier and the LNN-inspired contradiction engine.
        """
        return []

    #: fields compared between extractors during ensemble reconciliation
    ensemble_fields: list[str] = []
    #: flattened column names that identify a primary-table row (for gold-set joins
    #: that survive re-extraction). First column is the default stratification key.
    identity_columns: list[str] = []

    def ensemble_key(self, record: Any) -> Any:
        """Identity key for matching the same record across extractors.

        Return ``None`` to exclude a record type from ensemble reconciliation.
        """
        return None

    @abstractmethod
    def discover(self, source_entry: dict[str, Any]) -> list[CandidateDoc]:
        """Turn a registry source entry into concrete candidate documents."""

    @abstractmethod
    def extraction_schema(self) -> dict[str, Any]:
        """JSON schema describing the structured records to extract.

        Used as the tool ``input_schema`` for the LLM extractor.
        """

    @abstractmethod
    def extraction_instructions(self) -> str:
        """Static, cacheable instructions for the LLM extractor."""

    @abstractmethod
    def records_from_payload(
        self, payload: dict[str, Any], doc: SourceDocument, provenance_base: dict[str, Any]
    ) -> list[ExtractionRecord]:
        """Convert the LLM's JSON payload into typed, provenance-stamped records."""

    @abstractmethod
    def parse_rule_based(self, text: str, doc: SourceDocument) -> list[ExtractionRecord]:
        """Deterministic reference parser for the canonical text layout.

        This is *not* a general PDF parser -- real-world EQR PDFs are too
        heterogeneous for rules, which is exactly why the LLM extractor exists.
        It guarantees the pipeline (and CI) runs end-to-end with zero external
        dependencies, and serves as a labelled fixture for testing.
        """
