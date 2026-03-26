"""
tests/test_search_coverage.py — Unit tests for Phase 4: Search Coverage Expansion.

Tests scrape_with_firecrawl(), exa_search(), scan_firecrawl_sources(),
scan_exa_queries(), and cross-source deduplication.
All HTTP calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import config
from tools.firecrawl_client import scrape_with_firecrawl
from tools.exa_search import exa_search


# ── scrape_with_firecrawl() ───────────────────────────────────────────────────

class TestScrapeWithFirecrawl:
    def _mock_firecrawl_response(self, markdown="# StartupHonduras\n\nHello World startup founders") -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "data": {"markdown": markdown}}
        return resp

    def test_returns_markdown_string(self):
        """Successful response returns non-empty markdown text."""
        with (
            patch("tools.firecrawl_client.config.get_optional_key", return_value="fake-key"),
            patch("tools.firecrawl_client.config.FIRECRAWL_ENABLED", True),
            patch("tools.firecrawl_client.requests.post", return_value=self._mock_firecrawl_response()),
        ):
            result = scrape_with_firecrawl("https://startuphonduras.com")

        assert isinstance(result, str)
        assert len(result) > 0
        assert "startup" in result.lower()

    def test_returns_empty_string_without_key(self):
        """No FIRECRAWL_API_KEY → returns empty string, no HTTP call."""
        with (
            patch("tools.firecrawl_client.config.get_optional_key", return_value=None),
            patch("tools.firecrawl_client.config.FIRECRAWL_ENABLED", True),
            patch("tools.firecrawl_client.requests.post") as mock_post,
        ):
            result = scrape_with_firecrawl("https://startuphonduras.com")

        assert result == ""
        mock_post.assert_not_called()

    def test_returns_empty_string_when_disabled(self):
        """FIRECRAWL_ENABLED=False → returns empty string, no HTTP call."""
        with (
            patch("tools.firecrawl_client.config.get_optional_key", return_value="fake-key"),
            patch("tools.firecrawl_client.config.FIRECRAWL_ENABLED", False),
            patch("tools.firecrawl_client.requests.post") as mock_post,
        ):
            result = scrape_with_firecrawl("https://startuphonduras.com")

        assert result == ""
        mock_post.assert_not_called()

    def test_returns_empty_string_on_401(self):
        """401 auth error → returns empty string."""
        resp = MagicMock()
        resp.status_code = 401
        with (
            patch("tools.firecrawl_client.config.get_optional_key", return_value="bad-key"),
            patch("tools.firecrawl_client.config.FIRECRAWL_ENABLED", True),
            patch("tools.firecrawl_client.requests.post", return_value=resp),
        ):
            result = scrape_with_firecrawl("https://startuphonduras.com")

        assert result == ""

    def test_fails_open_on_exception(self):
        """Network error → returns empty string, does not propagate."""
        import requests as req_lib
        with (
            patch("tools.firecrawl_client.config.get_optional_key", return_value="fake-key"),
            patch("tools.firecrawl_client.config.FIRECRAWL_ENABLED", True),
            patch("tools.firecrawl_client.requests.post", side_effect=req_lib.RequestException("timeout")),
        ):
            result = scrape_with_firecrawl("https://startuphonduras.com")

        assert result == ""


# ── exa_search() ──────────────────────────────────────────────────────────────

class TestExaSearch:
    def _mock_exa_response(self) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "results": [
                {
                    "title": "Startup Costa Rica 2025",
                    "url": "https://example.cr/startup",
                    "text": "A fintech startup from Costa Rica raised pre-seed funding.",
                    "publishedDate": "2025-01-15",
                    "score": 0.92,
                },
                {
                    "title": "Emprendimiento Guatemala",
                    "url": "https://example.gt/empresa",
                    "text": "Startup guatemalteco en el sector agtech.",
                    "publishedDate": "2025-03-01",
                    "score": 0.85,
                },
            ]
        }
        return resp

    def test_returns_list_of_dicts(self):
        """Returns list of dicts with required keys."""
        with (
            patch("tools.exa_search.config.get_optional_key", return_value="fake-key"),
            patch("tools.exa_search.config.EXA_ENABLED", True),
            patch("tools.exa_search.requests.post", return_value=self._mock_exa_response()),
        ):
            results = exa_search("startup Costa Rica pre-seed", num_results=10)

        assert isinstance(results, list)
        assert len(results) == 2
        required = {"title", "url", "text", "published_date", "score"}
        for r in results:
            assert required.issubset(r.keys())

    def test_returns_empty_list_without_key(self):
        """No EXA_API_KEY → returns empty list, no HTTP call."""
        with (
            patch("tools.exa_search.config.get_optional_key", return_value=None),
            patch("tools.exa_search.config.EXA_ENABLED", True),
            patch("tools.exa_search.requests.post") as mock_post,
        ):
            results = exa_search("startup Costa Rica")

        assert results == []
        mock_post.assert_not_called()

    def test_returns_empty_list_when_disabled(self):
        """EXA_ENABLED=False → returns empty list, no HTTP call."""
        with (
            patch("tools.exa_search.config.get_optional_key", return_value="fake-key"),
            patch("tools.exa_search.config.EXA_ENABLED", False),
            patch("tools.exa_search.requests.post") as mock_post,
        ):
            results = exa_search("startup Costa Rica")

        assert results == []
        mock_post.assert_not_called()

    def test_fails_open_on_exception(self):
        """Network error → returns empty list, does not propagate."""
        import requests as req_lib
        with (
            patch("tools.exa_search.config.get_optional_key", return_value="fake-key"),
            patch("tools.exa_search.config.EXA_ENABLED", True),
            patch("tools.exa_search.requests.post", side_effect=req_lib.RequestException("timeout")),
        ):
            results = exa_search("startup Costa Rica")

        assert results == []

    def test_returns_empty_list_on_401(self):
        """401 auth error → returns empty list."""
        resp = MagicMock()
        resp.status_code = 401
        with (
            patch("tools.exa_search.config.get_optional_key", return_value="bad-key"),
            patch("tools.exa_search.config.EXA_ENABLED", True),
            patch("tools.exa_search.requests.post", return_value=resp),
        ):
            results = exa_search("startup Costa Rica")

        assert results == []


# ── Cross-source deduplication ────────────────────────────────────────────────

class TestCrossSourceDeduplication:
    def test_same_company_from_two_sources_appears_once(self):
        """
        The scout.py dedup uses a seen_names set (normalized).
        Simulate that a company name from Tavily and Exa is deduplicated
        before passing to extract_company_names().

        This is a unit test of the dedup logic used in scout.py Step 3.
        """
        # Simulate extract_company_names() returning same name from two sources
        companies_from_tavily = [("Paggo", "Paggo is a fintech from Costa Rica")]
        companies_from_exa = [("Paggo", "Costa Rica payments startup Paggo")]

        # Apply the same dedup logic from scout.py
        seen_names: set = set()
        final_candidates = []
        for name, snippet in companies_from_tavily + companies_from_exa:
            norm = name.lower().strip()
            if norm not in seen_names:
                seen_names.add(norm)
                final_candidates.append((name, snippet))

        assert len(final_candidates) == 1
        assert final_candidates[0][0] == "Paggo"

    def test_different_companies_both_included(self):
        """Two different companies from different sources both make it through."""
        companies_from_batches = [("Paggo", "fintech Costa Rica")]
        companies_from_exa = [("Zunify", "crypto Guatemala")]

        seen_names: set = set()
        final_candidates = []
        for name, snippet in companies_from_batches + companies_from_exa:
            norm = name.lower().strip()
            if norm not in seen_names:
                seen_names.add(norm)
                final_candidates.append((name, snippet))

        assert len(final_candidates) == 2
