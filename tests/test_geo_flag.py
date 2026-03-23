"""
tests/test_geo_flag.py — Unit tests for non_ca_founder_building_in_region flag.

Tests the logic that sets CompanyProfile.non_ca_founder_building_in_region.
We test the condition directly (not by calling enrich_with_claude which makes
LLM calls) since the flag-setting logic is a deterministic check.
"""

import pytest
from enrichment.engine import CompanyProfile, Founder


def _apply_flag(profile: CompanyProfile) -> bool:
    """Mirror the flag-setting logic from enrich_with_claude() for isolated testing."""
    import config

    _best_geo = max((f.geo_score for f in profile.founders), default=0)
    # Use full country names only — 2-letter codes (NI, SV, DO…) cause false positives
    # e.g. "NI" matches "united" as a substring. Mirrors engine.py fix.
    _region_lower = {c.lower() for c in config.TARGET_COUNTRIES.keys()}
    _text_lower = (
        (profile.one_liner or "")
        + " " + (profile.notes or "")
        + " " + (profile.country or "")
    ).lower()

    if _best_geo < 2 and any(c in _text_lower for c in _region_lower):
        profile.non_ca_founder_building_in_region = True

    return profile.non_ca_founder_building_in_region


def _make_founder(geo_score: int) -> Founder:
    f = Founder(name="Test Founder")
    f.geo_score = geo_score
    return f


def test_non_ca_founder_with_ca_company_sets_flag():
    """Founder with geo_score < 2 + 'Costa Rica' in one_liner → flag True."""
    profile = CompanyProfile(
        one_liner="Payments infrastructure company based in Costa Rica",
        founders=[_make_founder(geo_score=1)],
    )
    assert _apply_flag(profile) is True


def test_ca_founder_does_not_set_flag():
    """Founder with geo_score >= 2 should NOT set the flag."""
    profile = CompanyProfile(
        one_liner="Payments infrastructure in Costa Rica",
        founders=[_make_founder(geo_score=2)],
    )
    assert _apply_flag(profile) is False


def test_no_region_mention_does_not_set_flag():
    """Founder with geo_score < 2 but no region mention → flag False."""
    profile = CompanyProfile(
        one_liner="B2B enterprise software for global supply chains",
        country="United States",
        founders=[_make_founder(geo_score=0)],
    )
    assert _apply_flag(profile) is False


def test_flag_via_country_field():
    """Region mention in profile.country field should trigger the flag."""
    profile = CompanyProfile(
        one_liner="SaaS platform for SMEs",
        country="Guatemala",
        founders=[_make_founder(geo_score=0)],
    )
    assert _apply_flag(profile) is True


def test_flag_via_notes_field():
    """Region mention in profile.notes field should trigger the flag."""
    profile = CompanyProfile(
        one_liner="Supply chain analytics",
        notes="HQ in Panama City, Panama. Team of 8.",
        founders=[_make_founder(geo_score=1)],
    )
    assert _apply_flag(profile) is True


def test_flag_not_set_by_default():
    """Default CompanyProfile should have flag False."""
    profile = CompanyProfile()
    assert profile.non_ca_founder_building_in_region is False


def test_non_ca_founder_flag_in_notion_properties():
    """non_ca_founder_building_in_region must appear as checkbox in Notion payload."""
    from notion.writer import _build_page_properties
    from enrichment.engine import ThesisResult, ContactResult

    founder = Founder(name="External Founder")
    founder.geo_score = 1

    profile = CompanyProfile(
        name="RegionalCo",
        one_liner="Payments platform in Costa Rica",
        founders=[founder],
        thesis=ThesisResult(score=2, stars="⭐⭐", rationale="External founder"),
        contact=ContactResult(),
        non_ca_founder_building_in_region=True,
    )
    props = _build_page_properties(profile)
    assert "Non-CA Founder (Building in Region)" in props
    assert props["Non-CA Founder (Building in Region)"]["checkbox"] is True


def test_non_ca_flag_false_in_notion_properties():
    """When flag is False the Notion checkbox should be False."""
    from notion.writer import _build_page_properties
    from enrichment.engine import ThesisResult, ContactResult

    profile = CompanyProfile(
        name="LocalCo",
        founders=[Founder(name="CA Founder")],
        thesis=ThesisResult(score=4, stars="⭐⭐⭐⭐", rationale="CA founder"),
        contact=ContactResult(),
        non_ca_founder_building_in_region=False,
    )
    props = _build_page_properties(profile)
    assert props["Non-CA Founder (Building in Region)"]["checkbox"] is False
