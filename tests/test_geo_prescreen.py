"""
tests/test_geo_prescreen.py — Unit tests for the expanded geo_prescreen() function.
Covers all 4 geo signals: country names, city names, domain TLDs, university names.
No mocking needed — pure deterministic function.
"""

from monitor.batches import geo_prescreen


def test_country_name_passes():
    """Full country name in snippet → True."""
    assert geo_prescreen("Startup", "fintech company based in Costa Rica") is True


def test_country_name_dominican_republic_passes():
    """Multi-word country name works correctly."""
    assert geo_prescreen("Startup", "payments startup in the Dominican Republic") is True


def test_city_name_san_jose_passes():
    """San José (with accent) in snippet → True."""
    assert geo_prescreen("Startup", "headquartered in San José, growing fast") is True


def test_city_name_san_jose_no_accent_passes():
    """San Jose (no accent) in snippet → True."""
    assert geo_prescreen("Startup", "office in San Jose, Costa Rica") is True


def test_city_name_santo_domingo_passes():
    """Santo Domingo in snippet → True."""
    assert geo_prescreen("Startup", "Santo Domingo-based fintech platform") is True


def test_city_name_in_company_name_passes():
    """City name in company name itself (not just snippet) → True."""
    assert geo_prescreen("San Salvador Payments Co", "B2B fintech") is True


def test_domain_tld_cr_passes():
    """.cr TLD in snippet URL → True."""
    assert geo_prescreen("Startup", "visit us at https://company.cr/about") is True


def test_domain_tld_gt_passes():
    """.gt TLD in snippet URL → True."""
    assert geo_prescreen("Startup", "homepage: www.producto.gt") is True


def test_domain_tld_do_passes():
    """.do TLD in snippet → True (Dominican Republic)."""
    assert geo_prescreen("Startup", "see compania.do for details") is True


def test_university_incae_passes():
    """CA/DR university name in snippet → True."""
    assert geo_prescreen("Startup", "INCAE alumni building a payments platform") is True


def test_university_ucr_passes():
    """UCR in snippet → True."""
    assert geo_prescreen("Startup", "UCR graduate, founded in 2023") is True


def test_no_signal_returns_false():
    """No CA/DR signals → False (fail-closed)."""
    assert geo_prescreen("Startup", "B2B SaaS for US mid-market companies") is False


def test_non_ca_country_returns_false():
    """Non-CA/DR country name → False."""
    assert geo_prescreen("Startup", "London-based fintech startup") is False


def test_two_letter_code_ni_does_not_pass():
    """'NI' (Nicaragua 2-letter code) must NOT match 'united' — avoids false positives."""
    assert geo_prescreen("Startup", "B2B enterprise software for united states clients") is False


def test_empty_snippet_returns_false():
    """Empty name + snippet → False."""
    assert geo_prescreen("", "") is False
