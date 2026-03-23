"""
tests/test_portfolio_patterns.py — Data integrity checks for portfolio/patterns.py.
No mocking needed — pure data validation.
"""

from portfolio.patterns import PORTFOLIO_COMPANIES, PORTFOLIO_PATTERNS


REQUIRED_KEYS = {
    "name", "country", "category", "business_model",
    "revenue_model", "problem_domain", "founder_pattern",
    "stage_at_entry", "exit",
}


def test_company_count():
    assert len(PORTFOLIO_COMPANIES) == 12


def test_all_companies_have_required_keys():
    for company in PORTFOLIO_COMPANIES:
        missing = REQUIRED_KEYS - set(company.keys())
        assert not missing, f"{company.get('name', '?')} missing keys: {missing}"


def test_names_are_normalized():
    """All names should be lowercase (matching config.PORTFOLIO_COMPANIES set)."""
    for company in PORTFOLIO_COMPANIES:
        assert company["name"] == company["name"].lower(), (
            f"Name not normalized: {company['name']}"
        )


def test_portfolio_patterns_has_required_keys():
    required = {"sectors", "business_models", "revenue_models", "problem_domains",
                "top_sectors", "top_business_models", "top_domains"}
    missing = required - set(PORTFOLIO_PATTERNS.keys())
    assert not missing, f"PORTFOLIO_PATTERNS missing keys: {missing}"


def test_top_sectors_non_empty():
    assert len(PORTFOLIO_PATTERNS["top_sectors"]) > 0


def test_top_business_models_non_empty():
    """Signal 2 in portfolio_fit_score depends on this key."""
    assert len(PORTFOLIO_PATTERNS["top_business_models"]) > 0


def test_top_domains_non_empty():
    assert len(PORTFOLIO_PATTERNS["top_domains"]) > 0


def test_fintech_is_top_sector():
    """Fintech has the most portfolio companies and must appear in top_sectors."""
    assert "fintech" in PORTFOLIO_PATTERNS["top_sectors"]


def test_payments_in_top_domains():
    assert "payments" in PORTFOLIO_PATTERNS["top_domains"]
