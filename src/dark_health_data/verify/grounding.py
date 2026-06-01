"""Grounding / citation-consistency verifier.

The cheapest strong defense against hallucinated values: require the extractor to
return the verbatim source span each value was read from (``provenance.source_span``),
then deterministically check that (a) the span really occurs in the source document
text and (b) the record's salient numeric values actually appear in that span.

A cited span that is absent from the document, or that doesn't contain the number it
supposedly supports, is a hard failure -- a near-certain extraction error -- caught
with zero labels.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .signals import Signal

# document-level fields like reporting_year are not expected in a per-value cited
# span, so they are intentionally excluded from the salient-value hints.
_NUM_FIELDS_HINT = ("rate", "ratio", "numerator", "denominator", "deaths", "pct")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(",", "")).strip().lower()


def _salient_values(record: Any, fields: Optional[list[str]]) -> list[str]:
    dump = record.model_dump()
    out: list[str] = []
    for name, val in dump.items():
        if name in {"provenance", "qa_status", "qa_flags", "record_type", "trust_score", "review_recommended"}:
            continue
        if fields is not None and name not in fields:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            if fields is None and not any(h in name for h in _NUM_FIELDS_HINT):
                continue
            s = f"{val:g}" if isinstance(val, float) else str(val)
            out.append(s)
    return out


class GroundingVerifier:
    """Verify extracted values against their cited source spans."""

    def __init__(self, salient_fields: Optional[list[str]] = None):
        self.salient_fields = salient_fields

    def verify(self, records: list[Any], doc_texts: dict[str, str]) -> dict[int, list[Signal]]:
        signals: dict[int, list[Signal]] = {}
        for r in records:
            span = getattr(r.provenance, "source_span", None)
            if not span:
                signals[id(r)] = [Signal("grounding", score=None, ok=None, detail="no source span")]
                continue

            nspan = _norm(span)
            doc = doc_texts.get(r.provenance.source_document_id, "")
            span_in_doc = nspan in _norm(doc) if doc else None

            values = _salient_values(r, self.salient_fields)
            missing = [v for v in values if _norm(v) not in nspan]

            if span_in_doc is False:
                sig = Signal("grounding", score=0.0, ok=False,
                             detail="cited span not found in source document")
            elif missing:
                sig = Signal("grounding", score=0.1, ok=False,
                             detail=f"cited span omits value(s): {', '.join(missing)}")
            else:
                sig = Signal("grounding", score=1.0, ok=True,
                             detail="values grounded in cited span", weight=1.5)
            signals[id(r)] = [sig]
        return signals
