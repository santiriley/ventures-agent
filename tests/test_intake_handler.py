"""
tests/test_intake_handler.py — Unit tests for Phase 7: Inbound Intake Channel.

Tests handle_intake() for valid input, missing company, and light-enrich filtering.
All enrichment and Notion calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from intake.handler import handle_intake
from enrichment.engine import CompanyProfile, ThesisResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mock_profile(name="Paggo", thesis_score=4) -> CompanyProfile:
    profile = CompanyProfile(name=name)
    profile.thesis = ThesisResult(score=thesis_score, rationale="CA/DR tech startup")
    profile.notes = ""
    return profile


# ── handle_intake() ───────────────────────────────────────────────────────────

class TestHandleIntake:
    def test_valid_input_creates_notion_lead(self):
        """
        Valid company + light enrich passes → full enrichment runs → Notion push → "created".
        """
        profile = _mock_profile("Paggo", thesis_score=4)
        with (
            patch("intake.handler.light_enrich", return_value={
                "name": "Paggo", "country": "Costa Rica", "sector": "fintech",
                "stage": "seed", "has_ca_dr_signal": True, "skip_reason": None,
            }),
            patch("intake.handler.light_thesis_check", return_value=True),
            patch("intake.handler.enrich_with_claude", return_value=profile),
            patch("intake.handler.push_lead", return_value="created"),
        ):
            result = handle_intake("Paggo", referrer="LP Name", notes="met at INCAE")

        assert result["status"] == "created"
        assert result["company"] == "Paggo"
        assert result["thesis_score"] == 4
        assert result["skip_reason"] is None

    def test_missing_company_returns_error(self):
        """Empty company field → returns error status without calling enrichment."""
        with (
            patch("intake.handler.light_enrich") as mock_light,
        ):
            result = handle_intake("", referrer="LP")

        assert result["status"] == "error"
        assert result["company"] == ""
        assert "required" in result["skip_reason"]
        mock_light.assert_not_called()

    def test_whitespace_only_company_returns_error(self):
        """Whitespace-only company name → treated same as empty."""
        result = handle_intake("   ")
        assert result["status"] == "error"

    def test_filtered_by_light_enrich_returns_skipped(self):
        """
        light_thesis_check returns False → intake returns 'skipped'
        without calling enrich_with_claude or push_lead.
        """
        with (
            patch("intake.handler.light_enrich", return_value={
                "name": "Stripe", "country": "US", "sector": "fintech",
                "stage": "series-c", "has_ca_dr_signal": False,
                "skip_reason": "no CA/DR signal",
            }),
            patch("intake.handler.light_thesis_check", return_value=False),
            patch("intake.handler.enrich_with_claude") as mock_enrich,
            patch("intake.handler.push_lead") as mock_push,
        ):
            result = handle_intake("Stripe", referrer="LP")

        assert result["status"] == "skipped"
        assert result["skip_reason"] == "no CA/DR signal"
        mock_enrich.assert_not_called()
        mock_push.assert_not_called()

    def test_duplicate_company_returns_duplicate(self):
        """Company already in Notion → push_lead returns 'duplicate' → intake returns 'duplicate'."""
        profile = _mock_profile("Paggo")
        with (
            patch("intake.handler.light_enrich", return_value={
                "name": "Paggo", "has_ca_dr_signal": True, "skip_reason": None,
                "country": "CR", "sector": "fintech", "stage": "seed",
            }),
            patch("intake.handler.light_thesis_check", return_value=True),
            patch("intake.handler.enrich_with_claude", return_value=profile),
            patch("intake.handler.push_lead", return_value="duplicate"),
        ):
            result = handle_intake("Paggo")

        assert result["status"] == "duplicate"

    def test_enrichment_exception_returns_error(self):
        """Exception in enrich_with_claude → returns error status without raising."""
        with (
            patch("intake.handler.light_enrich", return_value={
                "name": "Paggo", "has_ca_dr_signal": True, "skip_reason": None,
                "country": "CR", "sector": "fintech", "stage": "seed",
            }),
            patch("intake.handler.light_thesis_check", return_value=True),
            patch("intake.handler.enrich_with_claude", side_effect=RuntimeError("API down")),
        ):
            result = handle_intake("Paggo")

        assert result["status"] == "error"
        assert "API down" in result["skip_reason"]

    def test_source_tag_includes_referrer(self):
        """enrich_with_claude is called with source='inbound:{referrer}'."""
        profile = _mock_profile("Paggo")
        with (
            patch("intake.handler.light_enrich", return_value={
                "name": "Paggo", "has_ca_dr_signal": True, "skip_reason": None,
                "country": "CR", "sector": "fintech", "stage": "seed",
            }),
            patch("intake.handler.light_thesis_check", return_value=True),
            patch("intake.handler.enrich_with_claude", return_value=profile) as mock_enrich,
            patch("intake.handler.push_lead", return_value="created"),
        ):
            handle_intake("Paggo", referrer="INCAE LP")

        call_kwargs = mock_enrich.call_args
        source_arg = call_kwargs[1].get("source") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else "")
        assert "inbound:INCAE LP" in source_arg
