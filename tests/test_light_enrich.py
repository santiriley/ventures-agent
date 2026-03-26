"""
tests/test_light_enrich.py — Unit tests for Phase 1: Tiered Enrichment.

Tests light_enrich() and light_thesis_check() without making real API calls.
All Anthropic + requests calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from enrichment.engine import light_enrich, light_thesis_check


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_claude_response(data: dict) -> MagicMock:
    """Build a mock Anthropic messages.create() response."""
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(data))]
    return msg


def _mock_tavily_response(snippets: list[str]) -> MagicMock:
    """Build a mock requests.post() response for Tavily basic search."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "results": [{"content": s} for s in snippets]
    }
    return resp


# ── light_enrich() ────────────────────────────────────────────────────────────

class TestLightEnrich:
    def test_returns_structured_dict(self):
        """All 6 required keys present in the returned dict."""
        haiku_data = {
            "name": "Paggo",
            "country": "Costa Rica",
            "sector": "Fintech",
            "stage": "seed",
            "has_ca_dr_signal": True,
        }
        with (
            patch("enrichment.engine.anthropic.Anthropic") as mock_anthropic,
            patch("enrichment.engine.config.get_optional_key", return_value="fake_tavily_key"),
            patch("enrichment.engine.requests") as mock_req,
        ):
            # requests is imported inside light_enrich as _req
            # We need to patch the requests used inside light_enrich
            pass

        # Simpler approach: patch at the point of use
        with (
            patch("enrichment.engine.anthropic.Anthropic") as mock_anthropic,
        ):
            client = MagicMock()
            client.messages.create.return_value = _mock_claude_response(haiku_data)
            mock_anthropic.return_value = client

            # Patch the requests.post inside light_enrich
            with patch("enrichment.engine.config.get_optional_key", return_value=None):
                result = light_enrich("Paggo startup fintech Costa Rica")

        required_keys = {"name", "country", "sector", "stage", "has_ca_dr_signal", "skip_reason"}
        assert required_keys.issubset(result.keys()), f"Missing keys: {required_keys - set(result.keys())}"

    def test_has_ca_dr_signal_true(self):
        """CA/DR startup sets has_ca_dr_signal=True."""
        haiku_data = {
            "name": "Paggo",
            "country": "Costa Rica",
            "sector": "Fintech",
            "stage": "seed",
            "has_ca_dr_signal": True,
        }
        with patch("enrichment.engine.anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            client.messages.create.return_value = _mock_claude_response(haiku_data)
            mock_anthropic.return_value = client

            with patch("enrichment.engine.config.get_optional_key", return_value=None):
                result = light_enrich("Paggo fintech Costa Rica")

        assert result["has_ca_dr_signal"] is True
        assert result["skip_reason"] is None

    def test_fails_open_on_json_error(self):
        """If Claude returns unparseable JSON, fail open (has_ca_dr_signal=True, skip_reason=None)."""
        with patch("enrichment.engine.anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            bad_msg = MagicMock()
            bad_msg.content = [MagicMock(text="Sorry, I cannot extract this")]
            client.messages.create.return_value = bad_msg
            mock_anthropic.return_value = client

            with patch("enrichment.engine.config.get_optional_key", return_value=None):
                result = light_enrich("Some company")

        assert result["has_ca_dr_signal"] is True
        assert result["skip_reason"] is None

    def test_fails_open_on_api_error(self):
        """If Claude raises an exception, fail open so the pipeline continues."""
        with patch("enrichment.engine.anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            client.messages.create.side_effect = Exception("API timeout")
            mock_anthropic.return_value = client

            with patch("enrichment.engine.config.get_optional_key", return_value=None):
                result = light_enrich("Some company")

        assert result["has_ca_dr_signal"] is True
        assert result["skip_reason"] is None


# ── light_thesis_check() ─────────────────────────────────────────────────────

class TestLightThesisCheck:
    def test_passes_ca_dr_tech_preseed(self):
        """CA/DR tech startup at pre-seed passes all checks."""
        light = {
            "name": "Paggo",
            "country": "Costa Rica",
            "sector": "Fintech",
            "stage": "pre-seed",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is True
        assert light["skip_reason"] is None

    def test_filters_no_ca_dr_signal(self):
        """Missing CA/DR signal → filtered."""
        light = {
            "name": "Stripe",
            "country": "United States",
            "sector": "Fintech",
            "stage": "seed",
            "has_ca_dr_signal": False,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is False
        assert light["skip_reason"] is not None
        assert "CA/DR" in light["skip_reason"]

    def test_filters_series_c(self):
        """Series C stage → filtered as outside fund mandate."""
        light = {
            "name": "Acme Corp",
            "country": "Costa Rica",
            "sector": "SaaS",
            "stage": "series-c",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is False
        assert "series-c" in light["skip_reason"].lower()

    def test_filters_growth_stage(self):
        """Growth stage → filtered."""
        light = {
            "name": "BigCo",
            "country": "Guatemala",
            "sector": "Fintech",
            "stage": "growth",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is False
        assert light["skip_reason"] is not None

    def test_filters_non_tech_sector(self):
        """Restaurant sector → filtered as non-tech."""
        light = {
            "name": "Pupuseria La Rica",
            "country": "El Salvador",
            "sector": "non-tech",
            "stage": "pre-seed",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is False
        assert "non-tech" in (light["skip_reason"] or "").lower()

    def test_filters_restaurant(self):
        """Explicit 'restaurant' sector string → filtered."""
        light = {
            "name": "Restaurante Tipico",
            "country": "Honduras",
            "sector": "restaurant",
            "stage": "seed",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is False

    def test_passes_unknown_stage(self):
        """Unknown stage is not filtered (fail open for ambiguous cases)."""
        light = {
            "name": "Stealth Startup",
            "country": "Panama",
            "sector": "SaaS",
            "stage": "unknown",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is True

    def test_filters_series_b(self):
        """Series B → filtered."""
        light = {
            "name": "MidCo",
            "country": "Dominican Republic",
            "sector": "Fintech",
            "stage": "series-b",
            "has_ca_dr_signal": True,
            "skip_reason": None,
        }
        assert light_thesis_check(light) is False
