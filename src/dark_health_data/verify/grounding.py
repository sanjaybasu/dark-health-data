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
    """Lowercase; drop thousands separators; every other non-alphanumeric run -> one space
    (so curly quotes, dashes, bullets, and line breaks do not break a match)."""
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return re.sub(r"[^0-9a-z]+", " ", text.lower()).strip()


_PAGE = re.compile(r"\[\[PAGE (\d+)\]\]")


def _pages_near(doc: str, page: Optional[int]) -> str:
    """Text of the cited page and its neighbours (the whole document when unknown)."""
    if not page:
        return doc
    parts = _PAGE.split(doc)
    keep = [parts[i + 1] for i in range(1, len(parts) - 1, 2)
            if abs(int(parts[i]) - page) <= 1]
    return " ".join(keep) if keep else doc


def _in_order_share(quote: list[str], text: list[str]) -> float:
    """Best share of quote tokens found in order within a window of 3x the quote length.

    A quote broken by a table cell, a running page footer, or a section number still
    scores near 1; a reworded quote does not.
    """
    if not quote or not text:
        return 0.0
    best, n, first = 0, len(quote), quote[0]
    for i, tok in enumerate(text):
        if tok != first:
            continue
        j, hits = 0, 0
        for t in text[i:i + 3 * n]:
            if j < n and t == quote[j]:
                j += 1
                hits += 1
        best = max(best, hits)
        if best == n:
            break
    return best / n


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
        norm_doc: dict[str, str] = {}  # normalized once per document, not once per record
        norm_near: dict[tuple[str, Optional[int]], list[str]] = {}
        for r in records:
            span = getattr(r.provenance, "source_span", None)
            if not span:
                signals[id(r)] = [Signal("grounding", score=None, ok=None, detail="no source span")]
                continue

            nspan = _norm(span)
            did = r.provenance.source_document_id
            doc = doc_texts.get(did, "")
            if doc and did not in norm_doc:
                norm_doc[did] = _norm(doc)
            span_in_doc = nspan in norm_doc[did] if doc else None
            gapped = False
            if span_in_doc is False:
                page = getattr(r.provenance, "page_start", None)
                if (did, page) not in norm_near:
                    norm_near[(did, page)] = _norm(_pages_near(doc, page)).split()
                if _in_order_share(nspan.split(), norm_near[(did, page)]) >= 0.9:
                    span_in_doc, gapped = True, True

            values = _salient_values(r, self.salient_fields)
            missing = [v for v in values if _norm(v) not in nspan]

            if span_in_doc is False:
                sig = Signal("grounding", score=0.0, ok=False,
                             detail="cited span not found in source document")
            elif missing:
                sig = Signal("grounding", score=0.1, ok=False,
                             detail=f"cited span omits value(s): {', '.join(missing)}")
            elif gapped:
                sig = Signal("grounding", score=0.8, ok=True, weight=1.5,
                             detail="cited span found with gaps (table cells or page break)")
            else:
                sig = Signal("grounding", score=1.0, ok=True,
                             detail="values grounded in cited span", weight=1.5)
            signals[id(r)] = [sig]
        return signals
