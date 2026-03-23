"""
portfolio/patterns.py — Structured data for the 12 well-documented Carica portfolio companies.

Used by:
  - enrichment/engine.py: portfolio_fit_score() for deterministic pattern matching
  - monitor/disruption.py: portfolio context for Claude trend prompts
  - config.py: PORTFOLIO_DERIVED_QUERIES generation

Schema per company:
  name          — normalized lowercase (matches config.PORTFOLIO_COMPANIES)
  country       — HQ country at time of investment
  category      — top-level sector (fintech / saas / marketplace / logistics / hardware)
  business_model — specific model string (used by Signal 2 in portfolio_fit_score)
  revenue_model  — how the company monetizes
  problem_domain — specific problem being solved
  founder_pattern — ca_founder | diaspora | non_ca_building_in_region
  stage_at_entry — investment stage
  exit           — acquisition/IPO details or None
"""

from __future__ import annotations

from collections import Counter

PORTFOLIO_COMPANIES: list[dict] = [
    {
        "name": "abaco",
        "country": "El Salvador",
        "category": "fintech",
        "business_model": "digital lending",
        "revenue_model": "net interest margin",
        "problem_domain": "sme credit",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "pre-seed",
        "exit": None,
    },
    {
        "name": "tumoni",
        "country": "Nicaragua",
        "category": "fintech",
        "business_model": "digital wallet",
        "revenue_model": "transaction fees",
        "problem_domain": "payments",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "onvo",
        "country": "Panama",
        "category": "fintech",
        "business_model": "payment acceptance",
        "revenue_model": "transaction fees",
        "problem_domain": "payments",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "zunify",
        "country": "Guatemala",
        "category": "fintech",
        "business_model": "payments network",
        "revenue_model": "transaction fees",
        "problem_domain": "payments",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": "Acquired by Evertec",
    },
    {
        "name": "osmo",
        "country": "Costa Rica",
        "category": "fintech",
        "business_model": "digital wallet",
        "revenue_model": "transaction fees",
        "problem_domain": "payments",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "avify",
        "country": "Costa Rica",
        "category": "saas",
        "business_model": "omnichannel commerce SaaS",
        "revenue_model": "subscription",
        "problem_domain": "commerce",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "snap compliance",
        "country": "Costa Rica",
        "category": "saas",
        "business_model": "regtech platform",
        "revenue_model": "subscription",
        "problem_domain": "compliance",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "socialdesk",
        "country": "El Salvador",
        "category": "saas",
        "business_model": "social commerce SaaS",
        "revenue_model": "subscription",
        "problem_domain": "commerce",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "vitrinnea",
        "country": "Costa Rica",
        "category": "marketplace",
        "business_model": "secondhand fashion marketplace",
        "revenue_model": "commission",
        "problem_domain": "circular economy",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "bee",
        "country": "Costa Rica",
        "category": "saas",
        "business_model": "loyalty points platform",
        "revenue_model": "subscription",
        "problem_domain": "loyalty",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "boxful",
        "country": "El Salvador",
        "category": "logistics",
        "business_model": "last-mile logistics",
        "revenue_model": "transaction fees",
        "problem_domain": "logistics",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
    {
        "name": "caldo",
        "country": "Costa Rica",
        "category": "hardware",
        "business_model": "kitchen automation",
        "revenue_model": "hardware plus subscription",
        "problem_domain": "kitchen automation",
        "founder_pattern": "ca_founder",
        "stage_at_entry": "seed",
        "exit": None,
    },
]

# ── Derived aggregate patterns ─────────────────────────────────────────────
# Built once at module load from PORTFOLIO_COMPANIES. Counter-based so it
# stays accurate automatically as companies are added.

_sectors         = Counter(c["category"]       for c in PORTFOLIO_COMPANIES)
_business_models = Counter(c["business_model"] for c in PORTFOLIO_COMPANIES)
_revenue_models  = Counter(c["revenue_model"]  for c in PORTFOLIO_COMPANIES)
_domains         = Counter(c["problem_domain"] for c in PORTFOLIO_COMPANIES)

PORTFOLIO_PATTERNS: dict = {
    "sectors":             _sectors,
    "business_models":     _business_models,
    "revenue_models":      _revenue_models,
    "problem_domains":     _domains,
    # top_* lists are used directly by portfolio_fit_score() signal matching
    "top_sectors":         [s  for s,  _ in _sectors.most_common(3)],
    "top_business_models": [bm for bm, _ in _business_models.most_common(5)],  # Signal 2
    "top_domains":         [d  for d,  _ in _domains.most_common(5)],
}
