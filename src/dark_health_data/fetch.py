"""Fetch + cache source documents, content-addressed by sha256.

Documents are immutable public records, so we cache by content hash: re-running
the pipeline never re-downloads unchanged files, and the hash doubles as the
stable ``document_id`` used throughout provenance.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .connectors.base import CandidateDoc
from .models import SourceDocument


def _read_local(path: str) -> bytes:
    return Path(path).read_bytes()


_USER_AGENTS = [
    # tried in order; servers vary -- some WAFs block spoofed browser UAs, others
    # block non-browser UAs, so we try a browser UA, a plain custom UA, then curl.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "dark-health-data/0.3 (+https://github.com/sanjaybasu/dark-health-data; research)",
    "curl/8.4.0",
]


def _download(url: str, timeout: int = 90) -> bytes:
    import requests  # available in base env

    last_exc: Exception | None = None
    for ua in _USER_AGENTS:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": ua, "Accept": "application/pdf,*/*;q=0.8"},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # try the next user-agent
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def fetch(candidate: CandidateDoc) -> SourceDocument:
    """Resolve a candidate to bytes (local or remote), cache it, return metadata."""
    settings.ensure_dirs()

    if candidate.local_path:
        # fixtures may be committed in-repo; resolve relative to repo root
        p = Path(candidate.local_path)
        if not p.is_absolute():
            p = settings.repo_root / p
        raw = _read_local(str(p))
        source = str(p)
        mime = "text/plain" if p.suffix.lower() == ".txt" else "application/pdf"
    elif candidate.url:
        raw = _download(candidate.url)
        source = candidate.url
        mime = "application/pdf"
    else:
        raise ValueError(f"Candidate has neither url nor local_path: {candidate!r}")

    doc_id = hashlib.sha256(raw).hexdigest()
    ext = Path(source).suffix or ".pdf"
    cached = settings.raw_dir / f"{doc_id}{ext}"
    if not cached.exists():
        cached.write_bytes(raw)

    doc = SourceDocument(
        document_id=doc_id,
        source_url=candidate.url,
        local_path=str(cached),
        title=candidate.title,
        publisher=candidate.publisher,
        dataset_id=candidate.dataset_id,
        jurisdiction=candidate.jurisdiction,
        program=candidate.program,
        report_year=candidate.report_year,
        retrieved_at=datetime.now(timezone.utc),
        mime_type=mime,
    )
    # write a provenance sidecar next to the cached bytes
    sidecar = settings.raw_dir / f"{doc_id}.meta.json"
    sidecar.write_text(doc.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    return doc
