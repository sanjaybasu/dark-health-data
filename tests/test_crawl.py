from dark_health_data.crawl import find_report_links

LANDING_HTML = """
<html><body>
  <h1>Managed Care Reports</h1>
  <ul>
    <li><a href="/docs/eqr-technical-report-2024.pdf">2024 External Quality Review Technical Report</a></li>
    <li><a href="reports/eqr_2023.pdf">EQR Annual Technical Report (2023)</a></li>
    <li><a href="https://example.gov/other/budget-2024.pdf">Annual Budget 2024</a></li>
    <li><a href="/about.html">About the program</a></li>
  </ul>
</body></html>
"""

KEYWORDS = ["external quality", "eqr", "technical report"]


def test_finds_only_matching_pdfs():
    links = find_report_links(LANDING_HTML, "https://health.example.gov/mc/", keywords=KEYWORDS)
    urls = [link["url"] for link in links]
    # the two EQR PDFs match; the budget PDF and the html page do not
    assert len(links) == 2
    assert "https://health.example.gov/docs/eqr-technical-report-2024.pdf" in urls
    assert "https://health.example.gov/mc/reports/eqr_2023.pdf" in urls
    assert all("budget" not in u for u in urls)


def test_infers_year_and_resolves_relative_urls():
    links = find_report_links(LANDING_HTML, "https://health.example.gov/mc/", keywords=KEYWORDS)
    by_year = {link["year"]: link for link in links}
    assert set(by_year) == {2023, 2024}
    assert by_year[2024]["url"].startswith("https://health.example.gov/")


def test_max_results_keeps_most_recent():
    # cap to 1 -> the newest (2024) report wins via prefer_recent ordering
    links = find_report_links(LANDING_HTML, "https://health.example.gov/mc/",
                              keywords=KEYWORDS, max_results=1)
    assert len(links) == 1
    assert links[0]["year"] == 2024
