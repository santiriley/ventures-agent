"""
tests/test_founder_dedup.py — Unit tests for Phase 6: Founder-level deduplication.

Tests _search_existing_by_founders() and push_lead() founder dedup logic.
All Notion API calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from notion.writer import _search_existing_by_founders, push_lead
from enrichment.engine import CompanyProfile, Founder


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_notion_results(pages: list[dict]) -> MagicMock:
    """Build a mock Notion query response with the given page dicts."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": pages}
    resp.raise_for_status = MagicMock()
    return resp


def _make_notion_page(page_id: str, name: str, status: str) -> dict:
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Status": {"select": {"name": status}},
        },
    }


def _profile_with_founders(*linkedin_urls: str) -> CompanyProfile:
    profile = CompanyProfile(name="NewCo")
    profile.founders = [Founder(name=f"Founder {i}", linkedin_url=url) for i, url in enumerate(linkedin_urls)]
    profile.founder_linkedin_urls = list(linkedin_urls)
    return profile


# ── _search_existing_by_founders() ────────────────────────────────────────────

class TestSearchExistingByFounders:
    def test_finds_match_for_known_linkedin_url(self):
        """Returns [{name, status, page_id}] when a Notion page contains the LinkedIn URL."""
        page = _make_notion_page("abc123", "OldCo", "Passed ❌")
        with patch("notion.writer.requests.post", return_value=_make_notion_results([page])):
            result = _search_existing_by_founders(["https://linkedin.com/in/founder1"])

        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "OldCo"
        assert result[0]["status"] == "Passed ❌"
        assert result[0]["page_id"] == "abc123"

    def test_returns_none_when_no_match(self):
        """Returns None if no Notion pages contain the LinkedIn URL."""
        with patch("notion.writer.requests.post", return_value=_make_notion_results([])):
            result = _search_existing_by_founders(["https://linkedin.com/in/nobody"])

        assert result is None

    def test_deduplicates_across_multiple_url_queries(self):
        """
        Same Notion page returned for two founder URL queries → appears once in result.
        """
        page = _make_notion_page("same-page", "SharedCo", "New 🆕")
        with patch("notion.writer.requests.post", return_value=_make_notion_results([page])):
            result = _search_existing_by_founders([
                "https://linkedin.com/in/founder-a",
                "https://linkedin.com/in/founder-b",
            ])

        assert result is not None
        assert len(result) == 1

    def test_runs_one_query_per_url(self):
        """Exactly N Notion API calls are made for N founder URLs."""
        empty_resp = _make_notion_results([])
        with patch("notion.writer.requests.post", return_value=empty_resp) as mock_post:
            _search_existing_by_founders([
                "https://linkedin.com/in/a",
                "https://linkedin.com/in/b",
                "https://linkedin.com/in/c",
            ])

        assert mock_post.call_count == 3

    def test_returns_none_for_empty_list(self):
        """Empty founder_urls → returns None without any API call."""
        with patch("notion.writer.requests.post") as mock_post:
            result = _search_existing_by_founders([])

        assert result is None
        mock_post.assert_not_called()

    def test_gracefully_skips_on_400_schema_missing(self):
        """
        400 status (property doesn't exist yet) → returns None, logs debug, no exception.
        Founder dedup is opt-in via NOTION_SETUP.md.
        """
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"message": "property not found"}
        with patch("notion.writer.requests.post", return_value=resp):
            result = _search_existing_by_founders(["https://linkedin.com/in/founder1"])

        assert result is None


# ── push_lead() founder dedup integration ────────────────────────────────────

class TestPushLeadFounderDedup:
    def _base_patches(self):
        """Common patches for push_lead() tests: no portfolio, no stage block."""
        return [
            patch("notion.writer._is_portfolio", return_value=False),
            patch("notion.writer._is_too_late_stage", return_value=False),
            patch("notion.writer._is_unknown_stage_but_overfunded", return_value=False),
            patch("notion.writer._search_existing", return_value=None),  # no name match
            patch("notion.writer.config.get_key", return_value="fake-notion-key"),
        ]

    def test_skips_when_founder_matches_portfolio_status(self):
        """Founder URL matches a Portfolio lead → push_lead returns 'portfolio', no page created."""
        profile = _profile_with_founders("https://linkedin.com/in/known-founder")
        founder_match = [{"name": "PortfolioCo", "status": "Portfolio ✅", "page_id": "p1"}]

        patches = self._base_patches() + [
            patch("notion.writer._search_existing_by_founders", return_value=founder_match),
        ]
        with _apply_patches(patches):
            result = push_lead(profile)

        assert result == "portfolio"

    def test_skips_when_founder_is_active_in_pipeline(self):
        """Founder URL matches an active (non-Passed, non-Portfolio) lead → returns 'duplicate'."""
        profile = _profile_with_founders("https://linkedin.com/in/active-founder")
        founder_match = [{"name": "ActiveCo", "status": "In Review 🔍", "page_id": "p2"}]

        patches = self._base_patches() + [
            patch("notion.writer._search_existing_by_founders", return_value=founder_match),
        ]
        with _apply_patches(patches):
            result = push_lead(profile)

        assert result == "duplicate"

    def test_pushes_with_reengage_note_when_founder_was_passed(self):
        """
        Founder URL matches a Passed ❌ lead → push_lead creates the page (returns 'created')
        with a re-evaluation note prepended to profile.notes.
        """
        profile = _profile_with_founders("https://linkedin.com/in/passed-founder")
        founder_match = [{"name": "OldCo", "status": "Passed ❌", "page_id": "p3"}]

        mock_create_resp = MagicMock()
        mock_create_resp.status_code = 200
        mock_create_resp.json.return_value = {"id": "new-page-id"}
        mock_create_resp.raise_for_status = MagicMock()

        patches = self._base_patches() + [
            patch("notion.writer._search_existing_by_founders", return_value=founder_match),
            patch("notion.writer.requests.post", return_value=mock_create_resp),
        ]
        captured_payload = {}

        def _capture_post(url, headers, json, timeout):
            if "/pages" in url:
                captured_payload.update(json)
            return mock_create_resp

        with _apply_patches(patches[:-1]):
            with patch("notion.writer.requests.post", side_effect=_capture_post):
                with patch("notion.writer._search_existing_by_founders", return_value=founder_match):
                    result = push_lead(profile)

        assert result == "created"
        notes_content = (
            captured_payload.get("properties", {})
            .get("Notes", {})
            .get("rich_text", [{}])[0]
            .get("text", {})
            .get("content", "")
        )
        assert "OldCo" in notes_content
        assert "Re-evaluate" in notes_content


# ── Helper ────────────────────────────────────────────────────────────────────

from contextlib import contextmanager, ExitStack

@contextmanager
def _apply_patches(patches):
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield
