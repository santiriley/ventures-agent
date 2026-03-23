"""
tests/conftest.py — Shared fixtures for Carica Scout test suite.
"""

import pytest
from enrichment.engine import CompanyProfile, Founder, ThesisResult, ContactResult


@pytest.fixture
def sample_profile():
    """A minimal CompanyProfile representing a fintech startup from Costa Rica."""
    founder = Founder(
        name="Ana García",
        geo_score=2,
        geo_signals=["University: UCR", "LinkedIn location: San José, Costa Rica"],
        location="San José, Costa Rica",
        university="UCR",
        company_country="Costa Rica",
    )
    return CompanyProfile(
        name="TestCo",
        website="https://testco.example.com",
        sector="Fintech",
        stage="seed",
        country="Costa Rica",
        one_liner="B2B payment acceptance platform for Central American merchants",
        founders=[founder],
        thesis=ThesisResult(score=4, stars="⭐⭐⭐⭐", rationale="CA/DR founder + tech + MVP"),
        contact=ContactResult(email="ana@testco.example.com", confidence="High"),
    )


@pytest.fixture
def saas_profile():
    """A SaaS profile with subscription revenue model."""
    return CompanyProfile(
        name="SaaSCo",
        sector="SaaS",
        stage="seed",
        country="Guatemala",
        one_liner="HR workforce management platform with subscription pricing",
    )


@pytest.fixture
def no_match_profile():
    """A profile that should match no portfolio signals."""
    return CompanyProfile(
        name="NanoMedCo",
        sector="Biotech",
        stage="pre-seed",
        country="Mexico",
        one_liner="Molecular diagnostics for rare diseases in clinical settings",
    )
