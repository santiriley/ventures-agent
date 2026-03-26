"""
tests/test_linkedin_enrich.py — Unit tests for Phase 3: LinkedIn Data Enrichment.

Tests fetch_linkedin_profile() and its integration with geo_score().
All HTTP calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import config
from tools.linkedin import fetch_linkedin_profile
from enrichment.engine import Founder, geo_score


# ── Helpers ───────────────────────────────────────────────────────────────────

def _proxycurl_response(
    full_name="Maria Lopez",
    headline="Co-founder & CTO",
    city="San José",
    country_full_name="Costa Rica",
    education=None,
    experiences=None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "full_name": full_name,
        "headline": headline,
        "city": city,
        "country_full_name": country_full_name,
        "education": education or [
            {
                "school": "UCR",
                "degree_name": "BSc Computer Science",
                "field_of_study": "CS",
                "starts_at": {"year": 2010},
                "ends_at": {"year": 2014},
            }
        ],
        "experiences": experiences or [
            {
                "company": "Paggo",
                "title": "CTO",
                "location": "San José, CR",
                "starts_at": {"year": 2020},
                "ends_at": None,
            }
        ],
    }
    return resp


# ── fetch_linkedin_profile() ─────────────────────────────────────────────────

class TestFetchLinkedinProfile:
    def test_returns_structured_dict(self):
        """Returns dict with all required keys when Proxycurl responds OK."""
        with (
            patch("tools.linkedin.config.get_optional_key", return_value="fake-key"),
            patch("tools.linkedin.config.LINKEDIN_ENRICH_ENABLED", True),
            patch("tools.linkedin.requests.get", return_value=_proxycurl_response()),
            patch("tools.linkedin.time.sleep"),
        ):
            result = fetch_linkedin_profile("https://linkedin.com/in/maria-lopez")

        assert result is not None
        required = {"full_name", "headline", "city", "country_full_name", "education", "experiences"}
        assert required.issubset(result.keys())
        assert result["country_full_name"] == "Costa Rica"
        assert result["city"] == "San José"
        assert len(result["education"]) == 1
        assert result["education"][0]["school"] == "UCR"

    def test_returns_none_without_api_key(self):
        """No PROXYCURL_API_KEY → returns None, no HTTP call."""
        with (
            patch("tools.linkedin.config.get_optional_key", return_value=None),
            patch("tools.linkedin.requests.get") as mock_get,
        ):
            result = fetch_linkedin_profile("https://linkedin.com/in/maria-lopez")

        assert result is None
        mock_get.assert_not_called()

    def test_returns_none_when_disabled(self):
        """LINKEDIN_ENRICH_ENABLED=False → returns None, no HTTP call."""
        with (
            patch("tools.linkedin.config.get_optional_key", return_value="fake-key"),
            patch("tools.linkedin.config.LINKEDIN_ENRICH_ENABLED", False),
            patch("tools.linkedin.requests.get") as mock_get,
        ):
            result = fetch_linkedin_profile("https://linkedin.com/in/maria-lopez")

        assert result is None
        mock_get.assert_not_called()

    def test_returns_none_on_401(self):
        """401 from Proxycurl → returns None (bad key)."""
        resp = MagicMock()
        resp.status_code = 401
        with (
            patch("tools.linkedin.config.get_optional_key", return_value="bad-key"),
            patch("tools.linkedin.config.LINKEDIN_ENRICH_ENABLED", True),
            patch("tools.linkedin.requests.get", return_value=resp),
            patch("tools.linkedin.time.sleep"),
        ):
            result = fetch_linkedin_profile("https://linkedin.com/in/someone")

        assert result is None

    def test_returns_none_on_404(self):
        """404 → profile not found → returns None."""
        resp = MagicMock()
        resp.status_code = 404
        with (
            patch("tools.linkedin.config.get_optional_key", return_value="fake-key"),
            patch("tools.linkedin.config.LINKEDIN_ENRICH_ENABLED", True),
            patch("tools.linkedin.requests.get", return_value=resp),
            patch("tools.linkedin.time.sleep"),
        ):
            result = fetch_linkedin_profile("https://linkedin.com/in/ghost")

        assert result is None

    def test_returns_none_on_exception(self):
        """Network error → returns None, does not propagate."""
        import requests as req_lib
        with (
            patch("tools.linkedin.config.get_optional_key", return_value="fake-key"),
            patch("tools.linkedin.config.LINKEDIN_ENRICH_ENABLED", True),
            patch("tools.linkedin.requests.get", side_effect=req_lib.RequestException("timeout")),
            patch("tools.linkedin.time.sleep"),
        ):
            result = fetch_linkedin_profile("https://linkedin.com/in/someone")

        assert result is None

    def test_ignores_non_linkedin_url(self):
        """Non-LinkedIn URL → returns None immediately."""
        with patch("tools.linkedin.requests.get") as mock_get:
            result = fetch_linkedin_profile("https://github.com/someone")

        assert result is None
        mock_get.assert_not_called()


# ── geo_score integration ────────────────────────────────────────────────────

class TestLinkedinGeoScoreImprovement:
    def test_proxycurl_ucr_improves_geo_score(self):
        """
        Founder with no signals initially; after Proxycurl returns UCR education
        and Costa Rica location, geo_score should be >= 2.
        """
        # Simulate what enrich_with_claude() does:
        # 1. Claude extracts a founder with no geo signals
        founder = Founder(
            name="Maria Lopez",
            linkedin_url="https://linkedin.com/in/maria-lopez",
            education=[],
            location="",
            university="",
            company_country="Costa Rica",
        )

        # 2. Proxycurl data is merged in
        lk = {
            "full_name": "Maria Lopez",
            "headline": "CTO",
            "city": "San José",
            "country_full_name": "Costa Rica",
            "education": [{"school": "UCR", "degree_name": "BSc CS", "field_of_study": "CS",
                           "starts_at": {"year": 2010}, "ends_at": {"year": 2014}}],
            "experiences": [{"company": "Paggo", "title": "CTO", "location": "San José, CR",
                             "starts_at": {"year": 2020}, "ends_at": None}],
        }
        # Apply the same merge logic as engine.py
        if lk.get("city") or lk.get("country_full_name"):
            parts = [p for p in [lk.get("city"), lk.get("country_full_name")] if p]
            founder.location = ", ".join(parts)
        if lk.get("education"):
            first_school = (lk["education"][0].get("school") or "").upper()
            for ca_uni in config.CA_DR_UNIVERSITIES:
                if ca_uni.upper() in first_school:
                    founder.university = ca_uni
                    break
        founder.linkedin_uncertain = False

        # 3. geo_score() runs
        geo_score(founder)

        assert founder.geo_score >= 2, (
            f"Expected geo_score >= 2, got {founder.geo_score}. Signals: {founder.geo_signals}"
        )
        assert founder.linkedin_uncertain is False
