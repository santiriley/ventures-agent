"""
config.py — All tunable settings for Carica Scout.
Secrets go in .env, not here.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
TMP_DIR = ROOT / ".tmp"
TMP_DIR.mkdir(exist_ok=True)

# ── Load secrets from .env ─────────────────────────────────────────────────
load_dotenv(ROOT / ".env")

REQUIRED_KEYS = {
    "ANTHROPIC_API_KEY": "console.anthropic.com → API Keys",
    "NOTION_API_KEY": "notion.so/my-integrations",
    "NOTION_DB_LEADS": "From Notion URL: notion.so/{THIS_PART}?v=...",
}

OPTIONAL_KEYS = {
    "NOTION_DB_EVENTS": "Second Notion database for events",
    "HUNTER_API_KEY": "hunter.io → free tier",
    "TAVILY_API_KEY": "app.tavily.com → API Keys (free tier available)",
}


def get_key(name: str) -> str:
    """Return an env var value or print a helpful error and raise."""
    value = os.environ.get(name, "").strip()
    if not value:
        url = REQUIRED_KEYS.get(name) or OPTIONAL_KEYS.get(name, "")
        print(f"\n⚠️  Missing required key: {name}")
        if url:
            print(f"   Get it at: {url}")
        print(f"   Add to:    .env file in project root\n")
        raise EnvironmentError(f"Missing required key: {name}")
    return value


def get_optional_key(name: str) -> str | None:
    """Return an optional env var or None (no error)."""
    return os.environ.get(name, "").strip() or None


# ── Claude model ───────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-opus-4-6"               # primary enrichment model
CLAUDE_MODEL_FAST = "claude-haiku-4-5-20251001" # cheap pass for filtering/outreach

# ── Fund thesis ────────────────────────────────────────────────────────────
FUND_NAME = "Carica VC"
MIN_TICKET_USD = 100_000

TARGET_STAGES = ["pre-seed", "seed", "series a"]

TARGET_COUNTRIES = {
    "Costa Rica":         "+506",
    "Guatemala":          "+502",
    "Honduras":           "+504",
    "El Salvador":        "+503",
    "Nicaragua":          "+505",
    "Panama":             "+507",
    "Dominican Republic": "+1809",  # primary DR prefix (no dash)
    "Belize":             "+501",
}

# All valid CA/DR phone prefixes — DR has three area codes
CA_DR_PHONE_PREFIXES: set[str] = set(TARGET_COUNTRIES.values()) | {"+1829", "+1849"}

CA_DR_COUNTRY_NAMES = set(TARGET_COUNTRIES.keys()) | {
    "CR", "GT", "HN", "SV", "NI", "PA", "DO", "BZ",
}

CA_DR_UNIVERSITIES = {
    "INCAE", "UCR", "TEC", "ULACIT", "UFM", "UVG", "URL",
    "UNITEC", "UCA", "UTP", "INTEC", "PUCMM", "UASD",
}

PORTFOLIO_COMPANIES = {
    "abaco", "alisto", "art", "avify", "azulo", "bee", "boxful",
    "caldo", "fitune", "harvie", "human", "indi", "kleantab", "mawi",
    "onvo", "osmo", "paggo", "pixdea", "sento", "siku",
    "snap compliance", "socialdesk", "tobi", "tumoni", "vitrinnea", "zunify",
}

# Legal suffixes to strip when normalizing company names for deduplication
LEGAL_SUFFIXES = (
    " inc", " inc.", " llc", " llc.", " ltd", " ltd.",
    " s.a.", " s.a.s.", " s.r.l.", " s.r.l", " corp", " corp.",
    " co.", " co", " srl", " sa",
)

# ── Thesis score labels ────────────────────────────────────────────────────
THESIS_SCORE_LABELS = {
    5: "⭐⭐⭐⭐⭐ — CA/DR founder + tech + MVP live + traction signals",
    4: "⭐⭐⭐⭐  — CA/DR founder + tech + MVP live",
    3: "⭐⭐⭐   — Likely CA/DR founder (2+ geo signals) + tech",
    2: "⭐⭐    — External founder clearly targeting region + tech",
    1: "⭐      — Weak signal — flag for manual analyst review",
}

# ── Deal pipeline stages ───────────────────────────────────────────────────
DEAL_STAGES = [
    "New 🆕",                  # Auto-set on push; not yet reviewed
    "Reviewing 🔍",             # Analyst has opened the record
    "Contacted 📧",             # First outreach sent
    "Meeting Scheduled 📅",    # Call booked
    "Active Interest ⚡",      # Post-meeting, fund wants to continue
    "Due Diligence 🔬",        # Deep dive underway
    "IC Memo 📄",              # Investment memo drafted
    "Portfolio ✅",             # Investment closed
    "Passed ❌",                # Declined; add Pass Reason in Notes
    "Stale ⏸",                 # No response after 3 follow-ups
]

# ── Monitor sources ────────────────────────────────────────────────────────
# Note: many modern directories require JS — pages that don't render via
# server-side HTML will yield empty results. Tavily (tools/research.py)
# is the more reliable path for research. Verify each URL works before
# adding to production.
ACCELERATOR_BATCH_URLS: list[str] = [
    # ── Global programs with strong LATAM presence ──
    "https://www.ycombinator.com/companies?batch=&regions=Latin+America",
    "https://500.co/thefund",

    # ── Regional accelerators & portfolios ──
    "https://www.nxtp.vc/portfolio",                    # NXTP Labs (key LATAM VC)
    "https://lavca.org/industry-data/vc-deal-data/",    # LAVCA deal tracker
    "https://endeavor.org/network/companies/",           # Endeavor global (filter CA)
    "https://startupchile.org/startups/",               # Start-Up Chile cohorts

    # ── IDB Lab & Finnovista ──
    # "https://idblab.iadb.org/en/portfolio",           # DNS failing — verify current URL
    "https://finnovista.com/en/portfolio/",

    # ── Google for Startups LATAM ──
    "https://startup.google.com/accelerator/latin-america/",

    # ── Dominican Republic ──
    "https://www.pucmm.edu.do/investigacion/incubadora",

    # ── To restore / verify (were DNS-failing as of 2026-03-10) ──
    # "https://endeavorguatemala.org/empresas/"         # Endeavor Guatemala — check if live
    # "https://endeavorcostarica.org/empresas/"         # Endeavor Costa Rica — check if live
    # "https://parquetec.ucr.ac.cr/emprendimiento/"     # UCR Parque Tec CR — check if live
]

NETWORK_PROFILE_URLS: list[str] = [
    # Add LinkedIn or public founder network pages to monitor
    # e.g. "https://www.linkedin.com/company/carica-vc/people/"
]

EVENT_CALENDAR_URLS: list[str] = [
    # ── Regional startup event calendars ──
    "https://idblab.iadb.org/en/events",
    "https://www.campusverde.cr/eventos/",
    # Note: Meetup/Eventbrite pages are JS-rendered and may not scrape cleanly
]

# ── Scraping / HTTP ────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 15          # seconds
REQUEST_DELAY = 1.5           # seconds between requests (be polite)
MAX_RETRIES = 3

USER_AGENT = (
    "Mozilla/5.0 (compatible; CaricaScout/1.0; +https://carica.vc)"
)

# ── Notion ─────────────────────────────────────────────────────────────────
NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

# ── Hunter.io ──────────────────────────────────────────────────────────────
HUNTER_BASE_URL = "https://api.hunter.io/v2"

# ── Tavily ─────────────────────────────────────────────────────────────────
TAVILY_SEARCH_DEPTH = "advanced"    # "basic" or "advanced"
TAVILY_MAX_RESULTS = 5

# ── Weekly monitor schedule ────────────────────────────────────────────────
MONITOR_CRON = "0 13 * * 1"   # Every Monday 07:00 Costa Rica (UTC-6) = 13:00 UTC
