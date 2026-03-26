"""
tests/test_traction_verify.py — Unit tests for Phase 5: Traction Verification.

Tests verify_traction(), TractionSnapshot, and each data source.
All HTTP calls and GitHub API calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.traction import verify_traction, TractionSnapshot, _check_app_store, _parse_days_ago
from enrichment.engine import CompanyProfile, Founder


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _profile_with_github(name="Paggo", github_url="https://github.com/paggo-app") -> CompanyProfile:
    profile = CompanyProfile(name=name)
    profile.founders = [
        Founder(name="Maria Lopez", github_url=github_url)
    ]
    return profile


def _profile_no_github(name="Paggo") -> CompanyProfile:
    profile = CompanyProfile(name=name)
    profile.founders = [
        Founder(name="Maria Lopez", github_url="")
    ]
    return profile


def _itunes_response(
    track_name="Paggo", rating=4.6, review_count=89
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [
            {
                "trackName": track_name,
                "averageUserRating": rating,
                "userRatingCount": review_count,
            }
        ]
    }
    resp.raise_for_status = MagicMock()
    return resp


# ── TractionSnapshot dataclass ───────────────────────────────────────────────

class TestTractionSnapshot:
    def test_default_snapshot_has_empty_signals(self):
        s = TractionSnapshot()
        assert s.verified_signals == []
        assert s.github_stars is None
        assert s.app_store_rating is None

    def test_snapshot_fields(self):
        s = TractionSnapshot(
            github_stars=142,
            github_last_commit_days=3,
            app_store_rating=4.6,
            app_store_reviews=89,
            verified_signals=["GitHub: 142 followers, last commit 3d ago", "iOS App Store: 4.6 ⭐ (89 reviews)"],
        )
        assert s.github_stars == 142
        assert len(s.verified_signals) == 2


# ── verify_traction() ─────────────────────────────────────────────────────────

class TestVerifyTraction:
    def test_returns_traction_snapshot(self):
        """verify_traction() always returns a TractionSnapshot, never raises."""
        profile = _profile_no_github()
        with (
            patch("tools.traction.config.TRACTION_VERIFY_ENABLED", True),
            patch("tools.traction.requests.get", return_value=MagicMock(status_code=404, json=MagicMock(return_value={"results": []}), raise_for_status=MagicMock())),
        ):
            result = verify_traction(profile)

        assert isinstance(result, TractionSnapshot)

    def test_github_stats_wired(self):
        """When founder has a github_url, GitHub stats are pulled via tools.github."""
        profile = _profile_with_github()
        mock_stats = {
            "username": "paggo-app",
            "public_repos": 12,
            "followers": 142,
            "top_languages": ["Python"],
            "last_active": "3 days ago",
            "profile_url": "https://github.com/paggo-app",
        }
        with (
            patch("tools.traction.config.TRACTION_VERIFY_ENABLED", True),
            patch("tools.traction._check_github", return_value={"stars": 142, "last_commit_days": 3, "repos": 12}),
            patch("tools.traction._check_app_store", return_value={}),
            patch("tools.traction._check_play_store", return_value={}),
        ):
            result = verify_traction(profile)

        assert result.github_stars == 142
        assert result.github_last_commit_days == 3
        assert any("GitHub" in s for s in result.verified_signals)

    def test_app_store_rating_populated(self):
        """App Store data populates app_store_rating and verified_signals."""
        profile = _profile_no_github()
        with (
            patch("tools.traction.config.TRACTION_VERIFY_ENABLED", True),
            patch("tools.traction._check_github", return_value={}),
            patch("tools.traction._check_app_store", return_value={"rating": 4.6, "reviews": 89}),
            patch("tools.traction._check_play_store", return_value={}),
        ):
            result = verify_traction(profile)

        assert result.app_store_rating == 4.6
        assert result.app_store_reviews == 89
        assert any("App Store" in s for s in result.verified_signals)

    def test_all_sources_fail_returns_empty_snapshot(self):
        """All sources error → TractionSnapshot with empty verified_signals. Never raises."""
        profile = _profile_no_github()
        with (
            patch("tools.traction.config.TRACTION_VERIFY_ENABLED", True),
            patch("tools.traction._check_github", return_value={}),
            patch("tools.traction._check_app_store", return_value={}),
            patch("tools.traction._check_play_store", return_value={}),
        ):
            result = verify_traction(profile)

        assert result.verified_signals == []
        assert result.github_stars is None
        assert result.app_store_rating is None

    def test_disabled_returns_empty_snapshot(self):
        """TRACTION_VERIFY_ENABLED=False → returns empty TractionSnapshot immediately."""
        profile = _profile_no_github()
        with (
            patch("tools.traction.config.TRACTION_VERIFY_ENABLED", False),
            patch("tools.traction._check_github") as mock_gh,
        ):
            result = verify_traction(profile)

        assert isinstance(result, TractionSnapshot)
        assert result.verified_signals == []
        mock_gh.assert_not_called()


# ── _check_app_store() ────────────────────────────────────────────────────────

class TestCheckAppStore:
    def test_returns_rating_on_match(self):
        """iTunes API returns a matching app → rating and reviews populated."""
        with patch("tools.traction.requests.get", return_value=_itunes_response("Paggo", 4.6, 89)):
            result = _check_app_store("Paggo")

        assert result.get("rating") == 4.6
        assert result.get("reviews") == 89

    def test_returns_empty_dict_on_network_error(self):
        """Network error → empty dict (fail open)."""
        import requests as req_lib
        with patch("tools.traction.requests.get", side_effect=req_lib.RequestException("timeout")):
            result = _check_app_store("Paggo")

        assert result == {}

    def test_returns_empty_dict_on_no_results(self):
        """iTunes returns empty results → empty dict."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": []}
        resp.raise_for_status = MagicMock()
        with patch("tools.traction.requests.get", return_value=resp):
            result = _check_app_store("Paggo")

        assert result == {}


# ── _parse_days_ago() ─────────────────────────────────────────────────────────

class TestParseDaysAgo:
    def test_parses_n_days_ago(self):
        assert _parse_days_ago("3 days ago") == 3
        assert _parse_days_ago("14 days ago") == 14

    def test_parses_iso_date(self):
        import datetime
        today = datetime.date.today()
        yesterday = (today - datetime.timedelta(days=1)).isoformat()
        result = _parse_days_ago(yesterday)
        assert result == 1

    def test_returns_none_for_empty(self):
        assert _parse_days_ago("") is None
        assert _parse_days_ago(None) is None  # type: ignore

    def test_returns_none_for_unparseable(self):
        assert _parse_days_ago("recently") is None
