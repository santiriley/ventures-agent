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
    "NOTION_DB_MARKET_INTEL": "Notion database for disruption intelligence memos (optional, no fallback)",
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
CLAUDE_MODEL_RESEARCH = "claude-sonnet-4-6"    # trend synthesis, disruption memos (1×/week)

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
    # DR additions (Phase 3b): UNPHU and UNIBE both have engineering/tech faculties
    "UNPHU", "UNIBE",
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

# ── Stage filtering ────────────────────────────────────────────────────────
# Stages that are outside the fund mandate (Pre-seed · Seed · Series A).
# Used by enrichment/engine.py (score cap) and notion/writer.py (hard gate).
# Single source of truth — import this constant; do not redefine it elsewhere.
OVER_STAGE_VALUES: frozenset[str] = frozenset({
    "series-b", "series b", "series-c", "series c",
    "series-d", "series d", "series-e", "series e",
    "growth", "public", "ipo", "pre-ipo", "late-stage",
})

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
# Source tags — short identifiers used in Notion Source field for quality tracking
# Maps each URL to a slug; used by monitor/batches.py when tagging discovered leads.
BATCH_URL_TAGS: dict[str, str] = {
    "https://www.ycombinator.com/companies?batch=&regions=Latin+America": "yc",
    "https://500.co/thefund": "500-global",
    "https://www.nxtp.vc/portfolio": "nxtp",
    "https://lavca.org/industry-data/vc-deal-data/": "lavca",
    "https://endeavor.org/network/companies/": "endeavor",
    "https://startupchile.org/startups/": "startup-chile",
    "https://bidlab.org/en": "idb-lab",
    "https://finnovista.com/en/portfolio/": "finnovista",
    "https://startup.google.com/accelerator/latin-america/": "google-startups",
    "https://www.pucmm.edu.do/investigacion/incubadora": "pucmm",
    "https://www.parquetec.org/en/proyectos": "parquetec",
    # "https://contxto.com/en/startups/": "contxto",  # removed 2026-03-23 — SSL fails on Python 3.9/LibreSSL; covered via tavily:contxto
    "https://iupana.com": "iupana",
    "https://www.seedstars.com/companies/": "seedstars",
    "https://vilcap.com/portfolio": "vilcap",
    "https://unreasonablegroup.com/companies/": "unreasonable",
}

NETWORK_URL_TAGS: dict[str, str] = {
    "https://carao.com/portfolio": "network:carao",
}

# Tavily query tags — same order as TAVILY_MONITOR_QUERIES below
TAVILY_QUERY_TAGS: list[str] = [
    "tavily:f6s",
    "tavily:producthunt",
    "tavily:dealroom",
    # Pre-raise signal queries (Tier 3 addition 2026-03-23)
    "tavily:demoDays",
    "tavily:cohorts",
    "tavily:govGrants",
    "tavily:uniIncubators",
    "tavily:contxto",
]

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
    "https://bidlab.org/en",                            # IDB Lab (migrated from idblab.iadb.org)
    "https://finnovista.com/en/portfolio/",

    # ── Google for Startups LATAM ──
    "https://startup.google.com/accelerator/latin-america/",

    # ── Dominican Republic ──
    "https://www.pucmm.edu.do/investigacion/incubadora",

    # ── ParqueTec Costa Rica ──
    "https://www.parquetec.org/en/proyectos",           # ParqueTec CR portfolio (was parquetec.ucr.ac.cr)

    # ── CA/DR tech media (Tier 1 addition 2026-03-12) ──
    # "https://contxto.com/en/startups/",                # removed 2026-03-23 — SSL fails on Python 3.9/LibreSSL; covered via tavily:contxto
    "https://iupana.com",                               # iupana — fintech-focused LATAM news

    # ── International accelerators with CA/DR deal flow (Tier 2 addition 2026-03-12) ──
    "https://www.seedstars.com/companies/",             # Seedstars — emerging markets, active in CA
    "https://vilcap.com/portfolio",                     # Village Capital — CA/DR programs
    "https://unreasonablegroup.com/companies/",         # Unreasonable Group — LATAM cohorts

    # ── Pre-raise signal sources (Tier 3 addition 2026-03-23) ──────────────
    # These surface companies before institutional funding, complementing the
    # post-investment directories above.
    # All candidates tested for HTML renderability — none survived:
    #   startuphonduras.com — JS SPA, no content in HTML
    #   senacyt.gob.pa — CAPTCHA/JS challenge
    #   accelerate2030.net/costa-rica — HTTP 500
    #   nacionemprendedora.go.cr — HTTP 404
    #   kec.ufm.edu — static HTML but no company listings, only programs/events
    #   startup.google.com/accelerator/latino-founders — alumni (post-raise)
    #   procomer.com/programa-impulso-tecnologico — winners in PDF, not scrape-able
    #   tec.ac.cr/emprendimientos-apoyados-tec-emprende-lab — static HTML with company
    #     names, but they appear after char ~4400; extract_company_names() only passes
    #     the first 4000 chars to Claude (navigation fills those). Surfaced via
    #     tavily:uniIncubators query instead.
    # Pre-raise deal flow is sourced via the 4 new Tavily queries below.

    # ── Removed (no active portal confirmed as of 2026-03-10) ──
    # "https://endeavorguatemala.org/empresas/"         # Endeavor GT — org closed regional site
    # "https://endeavorcostarica.org/empresas/"         # Endeavor CR — org closed regional site
]

NETWORK_PROFILE_URLS: list[str] = [
    # ── Regional VC portfolios — signals fundable CA/DR companies (Tier 1 addition 2026-03-12) ──
    "https://carao.com/portfolio",                      # Carao Ventures — CR-based seed VC
    # Add HIVED, Wollef, SV VC portfolio URLs here once confirmed
]

# ── Tavily monitor queries — for JS-heavy sites that don't render via BeautifulSoup ──
# Used by monitor/batches.py:scan_tavily_queries() during the weekly run.
# Results are passed through Claude (fast model) for company name extraction, same as batch pages.
TAVILY_MONITOR_QUERIES: list[str] = [
    # F6S — early-stage startup applications (keep queries short; Tavily is semantic, not Google)
    "early stage pre-seed seed startup founder Costa Rica Guatemala Honduras El Salvador Panama Nicaragua Dominican Republic",
    # ProductHunt — recent product launches by LATAM founders
    "ProductHunt new startup launch Costa Rica Guatemala Honduras El Salvador Panama Dominican Republic 2024 2025",
    # Dealroom — seed stage Central America Caribbean
    "seed pre-seed startup Central America Dominican Republic early stage 2023 2024 2025",
    # ── Pre-raise signal queries (Tier 3 addition 2026-03-23) ──────────────
    # Demo days and pitch competitions — companies presenting publicly = pre-raise signal
    "demo day pitch competition startup Costa Rica Guatemala Honduras El Salvador Panama Dominican Republic 2025 2026",
    # Accelerator cohort acceptance announcements — first public signal a company exists
    "accelerator cohort accepted startup Central America Dominican Republic 2025 2026",
    # Government innovation grants — PROCOMER (CR) and SENACYT (PA) publish grant winners
    "PROCOMER SENACYT grant startup tecnología 2025 2026",
    # University incubator cohorts — Spanish terms surface regional announcements
    "incubadora startup cohorte emprendimiento Costa Rica Guatemala Honduras Panama",
    # Contxto — primary CA/DR startup media; moved from batch scraping (SSL fails on Python 3.9/LibreSSL)
    "Contxto startup Central America Dominican Republic pre-seed seed 2025 2026",
]
# NOTE: Operator-heavy queries (site:, OR chains, negative terms) degrade Tavily's semantic
# search quality. Keep these short. Late-stage results are filtered post-retrieval by
# stage_prescreen() in monitor/batches.py before they reach Claude for name extraction.

# ── Portfolio-derived queries (Phase 3a) ───────────────────────────────────
# Sectors directly represented in the Carica portfolio. These mirror active
# investments: fintech (ONVO, Zunify, Ábaco, O$MO), commerce SaaS (Avify,
# Socialdesk), logistics (Boxful), regtech (Snap Compliance).
PORTFOLIO_DERIVED_QUERIES: list[str] = [
    "fintech payments startup Costa Rica Guatemala Honduras El Salvador Panama Dominican Republic seed pre-seed 2025 2026",
    "payments acceptance startup Central America Dominican Republic founder seed 2025",
    "commerce omnichannel SaaS startup Central America Dominican Republic 2025 2026",
    "logistics last-mile startup Costa Rica Panama Guatemala seed 2025 2026",
    "regtech compliance GRC startup Latin America seed pre-seed 2025 2026",
]
PORTFOLIO_DERIVED_TAGS: list[str] = [
    "tavily:port-fintech",
    "tavily:port-payments",
    "tavily:port-commerce",
    "tavily:port-logistics",
    "tavily:port-regtech",
]

# ── Thesis-adjacent queries (Phase 3a) ────────────────────────────────────
# Sectors NOT yet in portfolio but within fund thesis. Expansion bets —
# treat results as lower confidence than portfolio-derived queries.
THESIS_ADJACENT_QUERIES: list[str] = [
    "HR workforce SaaS startup Central America Dominican Republic 2025 2026",
    "marketplace B2B startup Central America pre-seed 2025 2026",
    "circular economy secondhand marketplace startup Latin America 2025 2026",
]
THESIS_ADJACENT_TAGS: list[str] = [
    "tavily:thesis-hr",
    "tavily:thesis-marketplace",
    "tavily:thesis-circular",
]

# ── Dominican Republic specific queries (Phase 3b) ────────────────────────
DR_SPECIFIC_QUERIES: list[str] = [
    "startup tecnología emprendimiento República Dominicana Santo Domingo 2025 2026",
    "fintech startup Dominican Republic founder seed pre-seed 2025",
    "INTEC PUCMM UNPHU startup emprendimiento Dominican Republic 2025",
]
DR_SPECIFIC_TAGS: list[str] = [
    "tavily:dr-startup",
    "tavily:dr-fintech",
    "tavily:dr-universities",
]

# Merge all new query lists into TAVILY_MONITOR_QUERIES (maintains parallel-list invariant)
TAVILY_MONITOR_QUERIES = (
    TAVILY_MONITOR_QUERIES
    + PORTFOLIO_DERIVED_QUERIES
    + THESIS_ADJACENT_QUERIES
    + DR_SPECIFIC_QUERIES
)
TAVILY_QUERY_TAGS = (
    TAVILY_QUERY_TAGS
    + PORTFOLIO_DERIVED_TAGS
    + THESIS_ADJACENT_TAGS
    + DR_SPECIFIC_TAGS
)

EVENT_CALENDAR_URLS: list[str] = [
    # ── Regional startup event calendars ──
    "https://events.iadb.org/",                         # IDB Lab events (migrated from idblab.iadb.org)
    # "https://www.campusverde.cr/eventos/",  # removed 2026-03-23 — domain is dead
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

# ── Email notifications (optional) ────────────────────────────────────────
# Set NOTIFY_EMAIL_ENABLED=true in .env to activate post-run summaries.
# GMAIL_APP_PASSWORD is NOT your Google account password — generate one at:
# myaccount.google.com/apppasswords  (requires 2FA to be enabled)
NOTIFY_EMAIL_ENABLED: bool = os.environ.get("NOTIFY_EMAIL_ENABLED", "").strip().lower() == "true"
NOTIFY_EMAIL_TO: str | None = os.environ.get("NOTIFY_EMAIL_TO", "").strip() or None
NOTIFY_EMAIL_FROM: str | None = os.environ.get("NOTIFY_EMAIL_FROM", "").strip() or None
GMAIL_APP_PASSWORD: str | None = os.environ.get("GMAIL_APP_PASSWORD", "").strip() or None
