"""Extractor interface.

An extractor turns the *text* of one source document into typed records using a
connector's schema/instructions. Two implementations ship:

* ``RuleExtractor`` -- deterministic, zero-dependency; used for the demo, CI, and
  as a labelled fixture. See ``Connector.parse_rule_based``.
* ``LLMExtractor`` -- Claude structured extraction; this is what generalizes to
  real, heterogeneous PDFs.

Both return ``ExtractionRecord`` objects already stamped with provenance, so the
rest of the pipeline never needs to know which extractor ran.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..connectors.base import Connector
from ..models import ExtractionRecord, SourceDocument


class Extractor(ABC):
    name: str = "base"

    @abstractmethod
    def extract(
        self, text: str, doc: SourceDocument, connector: Connector
    ) -> list[ExtractionRecord]:
        ...


class RuleExtractor(Extractor):
    name = "rule"

    def extract(
        self, text: str, doc: SourceDocument, connector: Connector
    ) -> list[ExtractionRecord]:
        return connector.parse_rule_based(text, doc)
