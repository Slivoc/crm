import unittest
from unittest.mock import patch

from services.customer_news_ingestion import (
    SourceRateLimitedError,
    _repair_common_xml_errors,
    fetch_gdelt_source,
    fetch_rss_source,
)


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None, payload=None):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class NewsSourceFetchingTests(unittest.TestCase):
    def test_repairs_bare_ampersand_and_xml_control_character(self):
        repaired = _repair_common_xml_errors("Fish & Chips\x0b &amp; More")
        self.assertEqual(repaired, "Fish &amp; Chips &amp; More")

    @patch("services.customer_news_ingestion._http_get")
    def test_rss_fetch_recovers_common_malformed_xml(self, mock_get):
        mock_get.return_value = FakeResponse(
            "<rss><channel><title>Test</title><item><title>A & B</title>"
            "<link>https://example.com/a?x=1&amp;y=2</link></item></channel></rss>"
        )

        articles = fetch_rss_source({"id": 1, "name": "Test", "url": "https://example.com/feed"})

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "A & B")

    @patch("services.customer_news_ingestion._http_get")
    def test_gdelt_429_has_actionable_message(self, mock_get):
        mock_get.return_value = FakeResponse(status_code=429, headers={"Retry-After": "60"})

        with self.assertRaisesRegex(SourceRateLimitedError, "remaining GDELT sources were skipped.*60 seconds"):
            fetch_gdelt_source({"query": '"NHV" helicopter'})


if __name__ == "__main__":
    unittest.main()
