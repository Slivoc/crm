import unittest
from unittest.mock import patch

from services.customer_news_ingestion import (
    SourceRateLimitedError,
    _repair_common_xml_errors,
    delete_all_news_sources,
    delete_all_news_articles,
    delete_news_articles,
    delete_news_source,
    delete_news_sources,
    fetch_gdelt_source,
    fetch_rss_source,
    discover_webpage_feeds,
    due_sources,
    list_recent_articles,
    selection_diagnostics,
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
    @patch("services.customer_news_ingestion.db_execute")
    def test_due_sources_can_force_active_sources_regardless_of_schedule(self, mock_execute):
        mock_execute.return_value = []

        due_sources(source_type="rss", force=True)

        query = mock_execute.call_args.args[0]
        self.assertIn("active = TRUE", query)
        self.assertIn("source_type = ?", query)
        self.assertNotIn("last_checked_at <=", query)

    @patch("services.customer_news_ingestion.db_execute")
    def test_due_sources_normally_honours_check_frequency(self, mock_execute):
        mock_execute.return_value = []

        due_sources(source_type="rss")

        self.assertIn("last_checked_at <=", mock_execute.call_args.args[0])

    @patch("services.customer_news_ingestion.db_execute")
    def test_selection_diagnostics_exports_dashboard_and_nightly_snapshots(self, mock_execute):
        mock_execute.side_effect = [
            {"selected_distinct_articles": 2},
            [{"article_id": 11, "title": "Dashboard story"}],
            [{"salesperson_id": 3, "selected_for_nightly_precheck": 1}],
            [{"salesperson_id": 3, "article_id": 12, "item_rank": 1}],
        ]

        diagnostics = selection_diagnostics()

        self.assertEqual(diagnostics["dashboard"]["counts"]["selected_distinct_articles"], 2)
        self.assertEqual(diagnostics["dashboard"]["selected_articles"][0]["article_id"], 11)
        self.assertEqual(diagnostics["nightly_email"]["salespeople"][0]["salesperson_id"], 3)
        self.assertEqual(diagnostics["nightly_email"]["selected_articles_precheck"][0]["article_id"], 12)
        self.assertIn("semantic duplicate", diagnostics["nightly_email"]["selection_rules"]["final_filter_note"])

    @patch("services.customer_news_ingestion.db_execute")
    def test_recent_articles_default_to_customer_matches(self, mock_execute):
        mock_execute.return_value = []

        list_recent_articles()

        query = mock_execute.call_args.args[0]
        self.assertIn("HAVING COUNT(acm.id) > 0", query)
        self.assertIn("STRING_AGG(DISTINCT c.name", query)
        self.assertIn("MAX(acm.created_at) AS last_matched_at", query)
        self.assertIn("ORDER BY MAX(acm.created_at) DESC", query)

    @patch("services.customer_news_ingestion.db_execute")
    def test_all_articles_remain_ordered_by_publication_date(self, mock_execute):
        mock_execute.return_value = []

        list_recent_articles(matched_only=False)

        query = mock_execute.call_args.args[0]
        self.assertNotIn("HAVING COUNT(acm.id) > 0", query)
        self.assertIn("ORDER BY COALESCE(na.published_at, na.fetched_at) DESC", query)

    @patch("services.customer_news_ingestion.db_execute")
    def test_selected_articles_can_be_prioritized_before_limit(self, mock_execute):
        mock_execute.return_value = []

        list_recent_articles(prioritize_selected=True)

        query = mock_execute.call_args.args[0]
        order_by = query.split("ORDER BY", 1)[1]
        self.assertIn("EXISTS (SELECT 1 FROM tv_ranked", order_by)
        self.assertIn("EXISTS (SELECT 1 FROM nightly_ranked", order_by)

    @patch("services.customer_news_ingestion._upsert_source")
    @patch("services.customer_news_ingestion._http_get")
    def test_discovery_ignores_svg_and_article_links_with_rss_query(self, mock_get, mock_upsert):
        mock_get.return_value = FakeResponse(
            '<html><head>'
            '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
            '<link rel="alternate" type="application/rss+xml" href="/news.xml" title="News">'
            '</head><body>'
            '<a href="/article?f=/rss">Press release</a>'
            '<a href="/feeds/contracts">RSS contracts</a>'
            '</body></html>'
        )

        discover_webpage_feeds({"name": "Publisher", "url": "https://example.com/rss"})

        urls = [call.kwargs["url"] for call in mock_upsert.call_args_list]
        self.assertEqual(urls, ["https://example.com/news.xml", "https://example.com/feeds/contracts"])

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_source_retains_articles_via_foreign_key_behavior(self, mock_execute):
        mock_execute.return_value = {"id": 17}

        self.assertTrue(delete_news_source(17))
        mock_execute.assert_called_once_with(
            "DELETE FROM news_sources WHERE id = ? RETURNING id",
            (17,),
            fetch="one",
            commit=True,
        )

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_source_reports_missing_source(self, mock_execute):
        mock_execute.return_value = None

        self.assertFalse(delete_news_source(404))

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_selected_sources_deduplicates_ids(self, mock_execute):
        mock_execute.return_value = [{"id": 2}, {"id": 7}]

        self.assertEqual(delete_news_sources([7, 2, 7]), 2)
        mock_execute.assert_called_once_with(
            "DELETE FROM news_sources WHERE id IN (?, ?) RETURNING id",
            (2, 7),
            fetch="all",
            commit=True,
        )

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_all_sources(self, mock_execute):
        mock_execute.return_value = [{"id": 2}, {"id": 7}]

        self.assertEqual(delete_all_news_sources(), 2)
        mock_execute.assert_called_once_with(
            "DELETE FROM news_sources RETURNING id",
            fetch="all",
            commit=True,
        )

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_all_articles_reports_deleted_count(self, mock_execute):
        mock_execute.return_value = [{"id": 2}, {"id": 7}, {"id": 9}]

        self.assertEqual(delete_all_news_articles(), 3)
        mock_execute.assert_called_once_with(
            "DELETE FROM news_articles RETURNING id",
            fetch="all",
            commit=True,
        )

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_selected_articles_deduplicates_ids(self, mock_execute):
        mock_execute.return_value = [{"id": 2}, {"id": 7}]

        self.assertEqual(delete_news_articles([7, 2, 7]), 2)
        mock_execute.assert_called_once_with(
            "DELETE FROM news_articles WHERE id IN (?, ?) RETURNING id",
            (2, 7),
            fetch="all",
            commit=True,
        )

    @patch("services.customer_news_ingestion.db_execute")
    def test_delete_selected_articles_ignores_empty_selection(self, mock_execute):
        self.assertEqual(delete_news_articles([]), 0)
        mock_execute.assert_not_called()

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
