"""
tests/test_portfolio_fit.py — Unit tests for portfolio_fit_score().
No LLM calls, no I/O — pure function testing.
"""

import pytest
from enrichment.engine import CompanyProfile, portfolio_fit_score
from notion.writer import _build_page_properties
from enrichment.engine import ThesisResult, ContactResult, Founder


def test_returns_tuple():
    profile = CompanyProfile(sector="Fintech")
    result = portfolio_fit_score(profile)
    assert isinstance(result, tuple)
    assert len(result) == 2
    score, note = result
    assert isinstance(score, int)
    assert isinstance(note, str)


def test_fintech_sector_scores_signal_1():
    """A Fintech sector should fire Signal 1 (sector match)."""
    profile = CompanyProfile(sector="Fintech")
    score, note = portfolio_fit_score(profile)
    assert score >= 1
    assert "sector:fintech" in note


def test_saas_sector_scores_signal_1():
    """SaaS is a top portfolio sector and should fire Signal 1."""
    profile = CompanyProfile(sector="SaaS")
    score, note = portfolio_fit_score(profile)
    assert score >= 1
    assert "sector:saas" in note.lower()


def test_revenue_model_keyword_scores_signal_3():
    """One-liner with 'subscription' should fire Signal 3."""
    profile = CompanyProfile(
        sector="HRtech",
        one_liner="B2B HR platform with monthly subscription pricing",
    )
    score, note = portfolio_fit_score(profile)
    assert score >= 1
    assert "revenue:subscription" in note


def test_domain_match_scores_signal_4():
    """'payments' in one_liner should fire Signal 4 (domain match)."""
    profile = CompanyProfile(
        sector="Enterprise Software",
        one_liner="Helps merchants accept payments faster",
    )
    score, note = portfolio_fit_score(profile)
    assert score >= 1
    assert "domain:payments" in note


def test_no_match_returns_zero(no_match_profile):
    """A biotech company should match none of the portfolio signals."""
    score, note = portfolio_fit_score(no_match_profile)
    assert score == 0
    assert "no pattern match" in note


def test_score_capped_at_4():
    """Score cannot exceed 4 (4 signals max)."""
    # A profile crafted to hit all 4 signals
    profile = CompanyProfile(
        sector="Fintech",
        one_liner="digital lending platform offering subscription to SMEs for payments",
    )
    score, note = portfolio_fit_score(profile)
    assert score <= 4


def test_portfolio_fit_note_written_to_notion_properties():
    """portfolio_fit_note and portfolio_fit_score must appear in _build_page_properties output."""
    founder = Founder(name="Test User")
    profile = CompanyProfile(
        name="TestCo",
        sector="Fintech",
        stage="seed",
        country="Costa Rica",
        one_liner="payment acceptance for merchants",
        founders=[founder],
        thesis=ThesisResult(score=4, stars="⭐⭐⭐⭐", rationale="Test"),
        contact=ContactResult(email="test@testco.com", confidence="High"),
        portfolio_fit_score=2,
        portfolio_fit_note="Portfolio fit 2/4 — sector:fintech, domain:payments",
    )
    props = _build_page_properties(profile)
    assert "Portfolio Fit Score" in props
    assert props["Portfolio Fit Score"]["number"] == 2
    assert "Portfolio Fit Note" in props
    assert "Portfolio fit 2/4" in props["Portfolio Fit Note"]["rich_text"][0]["text"]["content"]


def test_empty_portfolio_fit_note_produces_valid_payload():
    """Empty portfolio_fit_note should produce a valid Notion payload (not null)."""
    founder = Founder(name="Test User")
    profile = CompanyProfile(
        name="TestCo",
        founders=[founder],
        thesis=ThesisResult(score=1, stars="⭐", rationale="Test"),
        contact=ContactResult(),
        portfolio_fit_score=0,
        portfolio_fit_note="",
    )
    props = _build_page_properties(profile)
    content = props["Portfolio Fit Note"]["rich_text"][0]["text"]["content"]
    assert content == ""  # empty string, not None
