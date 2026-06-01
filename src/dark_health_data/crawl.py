"""Lightweight link discovery for crawling agency landing pages.

Many states post their EQR technical reports on a landing page that links out to
the actual PDFs. ``find_report_links`` extracts candidate report links using only
the standard library (unit-tested against an HTML fixture). For states whose
listings are JavaScript-rendered (static HTML has no links), ``fetch_rendered_html``
renders the page with a headless browser (optional Playwright extra); feed its
output to ``find_report_links``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

YEAR_RE = re.compile(r"(19|20)\d{2}")
# href markers that indicate a downloadable document (gov CMS systems often serve
# PDFs via /download or /open endpoints with no .pdf extension)
_DOC_MARKERS = (".pdf", "/download", "/open", "/file", "attachment")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, link_text)
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None
            self._text = []


def find_report_links(
    html: str,
    base_url: str,
    *,
    keywords: list[str],
    require_pdf: bool = True,
    max_results: int | None = None,
    prefer_recent: bool = True,
) -> list[dict[str, str | int | None]]:
    """Return candidate report links from a landing page.

    A link qualifies if it points to a PDF (when ``require_pdf``) and either the
    href or the link text contains one of ``keywords`` (case-insensitive). The
    report year is inferred from the link text/href when present. When
    ``prefer_recent`` the results are sorted newest-first, and ``max_results`` caps
    how many are returned (useful so a page listing many years/MCOs doesn't explode
    into hundreds of documents).
    """
    parser = _LinkParser()
    parser.feed(html)

    kws = [k.lower() for k in keywords]
    out: list[dict[str, str | int | None]] = []
    seen: set[str] = set()
    for href, text in parser.links:
        url = urljoin(base_url, href)
        haystack = f"{href} {text}".lower()
        if require_pdf and not any(m in href.lower() for m in _DOC_MARKERS):
            continue
        if kws and not any(k in haystack for k in kws):
            continue
        if url in seen:
            continue
        seen.add(url)
        year_match = YEAR_RE.search(text) or YEAR_RE.search(href)
        out.append(
            {
                "url": url,
                "title": text or None,
                "year": int(year_match.group()) if year_match else None,
            }
        )
    if prefer_recent:
        out.sort(key=lambda d: (d["year"] is not None, d["year"] or 0), reverse=True)
    if max_results is not None:
        out = out[:max_results]
    return out


def fetch_rendered_html(url: str, *, timeout_ms: int = 45000, wait_until: str = "networkidle") -> str:
    """Render a JavaScript page with a headless browser and return the post-render HTML.

    Needed for states whose report listings are built client-side (the static crawler
    sees zero links). Requires ``pip install dark-health-data[crawl]`` and a one-time
    ``playwright install chromium``. Pair with ``find_report_links`` on the returned HTML.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "Headless crawling needs Playwright: `pip install dark-health-data[crawl]` "
            "then `playwright install chromium`."
        ) from exc

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            # ignore_https_errors: many agency sites (and proxied environments) present
            # cert chains a fresh Chromium won't trust; we only read public pages.
            context = browser.new_context(user_agent=ua, ignore_https_errors=True)
            page = context.new_page()
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # best effort; some pages never go fully idle
            return page.content()
        finally:
            browser.close()
