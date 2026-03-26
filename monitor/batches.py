"""
monitor/batches.py — Scan accelerator batch pages for new CA/DR companies.

Used by scout.py (Workflow B — Weekly Monitor).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

CACHE_FILE = config.TMP_DIR / "batches_cache.json"


def _load_cache() -> dict[str, list[str]]:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict[str, list[str]]) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _fingerprint(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _fetch_page_text(url: str) -> str:
    """Fetch and return visible text from a URL."""
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup.get_text(separator=" ", strip=True)
    except Exception as exc:
        logger.warning(f"Failed to fetch {url}: {exc}")
        return ""


_EXTRACT_PROMPT = """You are scanning an accelerator or VC portfolio page for individual startup companies.

Extract every startup/company name you can identify from the text.
For each company, also capture a short context snippet (1-2 sentences) from the surrounding text near the company name.
Exclude: the accelerator itself, VC funds, government programs, blogs, events, and non-companies.

Return ONLY a JSON array of objects. Example:
[{"name": "Acme", "snippet": "Acme is a Costa Rica-based fintech startup founded by Maria Gomez."}, {"name": "Beta", "snippet": "Beta was founded by a Guatemalan team to solve logistics."}]
If no startups are found, return an empty array: []"""


def extract_company_names(page_text: str) -> list[tuple[str, str]]:
    """
    Use Claude (fast model) to extract startup names and context snippets from a batch page.
    Returns a list of (name, snippet) tuples. Snippet is used for geo pre-screening.
    """
    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
    try:
        message = client.messages.create(
            model=config.CLAUDE_MODEL_FAST,
            max_tokens=1024,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": page_text[:4000]}],
        )
        raw = message.content[0].text.strip()
        bracket_start = raw.find("[")
        bracket_end = raw.rfind("]")
        if bracket_start != -1 and bracket_end > bracket_start:
            raw = raw[bracket_start:bracket_end + 1]
        items = json.loads(raw)
        if isinstance(items, list):
            result = []
            for item in items:
                if isinstance(item, dict):
                    name = (item.get("name") or "").strip()
                    snippet = (item.get("snippet") or "").strip()
                    if name:
                        result.append((name, snippet))
                elif isinstance(item, str) and item.strip():
                    # Backward compat: plain string with no snippet
                    result.append((item.strip(), ""))
            return result
    except Exception as exc:
        logger.warning(f"Company name extraction failed: {exc}")
    return []


# ── Late-stage snippet filter ─────────────────────────────────────────────────
# Matches strong late-stage signals: Series B+, $10M+ amounts, IPO, unicorn.
# "$2M seed round" won't match (requires 2+ digits before M).
_LATE_STAGE_PATTERNS = re.compile(
    r"series\s*[b-z]|"
    r"\$\d{2,4}\s*m(?:illion)?|"
    r"(?:raised|funding|round)\s+\$?\d{2,4}\s*m|"
    r"\bipo\b|\bpre-ipo\b|\bgrowth[\s-]stage\b|\blate[\s-]stage\b|"
    r"\bunicorn\b|\bdecacorn\b",
    re.IGNORECASE,
)


def stage_prescreen(name: str, snippet: str) -> bool:
    """
    Return True if the company appears early-stage (safe to proceed to enrichment).
    Return False if late-stage signals are detected in the name+snippet text.

    Deterministic — no API call. Conservative: only blocks on strong signals.
    A $2M seed mention will NOT trigger this (requires 2+ digit dollar amounts).
    Fails open: returns True when snippet is empty or ambiguous.
    """
    text = f"{name} {snippet}"
    return not bool(_LATE_STAGE_PATTERNS.search(text))


def funding_precheck(company_name: str) -> str | None:
    """
    Run one cheap Tavily basic search to check for late-stage funding signals
    before committing to full enrichment (5 advanced searches + Claude Opus call).

    Returns a reason string if the company should be skipped, or None if safe to enrich.
    Fails open: returns None on any error or missing API key.

    Cost savings: catches ~1 Tavily advanced search + 1 Opus call per blocked company.
    """
    tavily_key = config.get_optional_key("TAVILY_API_KEY")
    if not tavily_key:
        return None  # fail open

    import requests as _requests
    try:
        payload = {
            "api_key": tavily_key,
            "query": f"{company_name} funding raised series",
            "search_depth": "basic",
            "max_results": 3,
            "include_answer": False,
            "include_raw_content": False,
        }
        resp = _requests.post(
            "https://api.tavily.com/search", json=payload, timeout=config.REQUEST_TIMEOUT
        )
        if resp.status_code != 200:
            return None  # fail open

        results = resp.json().get("results", [])
        combined = " ".join(
            f"{r.get('title', '')} {r.get('content', '')}" for r in results
        ).lower()

        match = _LATE_STAGE_PATTERNS.search(combined)
        if match:
            return f"funding precheck: '{match.group()}' found in search results"
        return None  # safe to enrich

    except Exception as exc:
        logger.warning(f"Funding precheck failed for {company_name}: {exc}")
        return None  # fail open
    finally:
        time.sleep(config.REQUEST_DELAY)


def geo_prescreen(name: str, snippet: str) -> bool:
    """
    Return True if at least 1 CA/DR geo signal is present in the combined name+snippet text.
    Deterministic — no API call. Fail-closed: returns False if no signal found.

    Checks (in priority order):
    1. Full country names (most reliable — e.g. "Costa Rica", "Dominican Republic")
    2. CA/DR city names (e.g. "San José", "Santo Domingo")
    3. Country-code TLDs in URLs (e.g. ".cr", ".gt")
    4. CA/DR university names — word-boundary match only (prevents "TEC" matching "fintech",
       "URL" matching web URLs, "UCA" matching "education")
    """
    text = (name + " " + snippet).lower()

    # Signal 1: full country names (2-letter codes excluded — too short, cause false positives)
    if any(c.lower() in text for c in config.TARGET_COUNTRIES.keys()):
        return True

    # Signal 2: CA/DR city names
    if any(city in text for city in config.CA_DR_CITY_NAMES):
        return True

    # Signal 3: country-code TLDs (matches ".cr" in "https://company.cr/about")
    if any(tld in text for tld in config.CA_DR_DOMAIN_TLDS):
        return True

    # Signal 4: CA/DR university names — word-boundary only to avoid substring false positives
    # e.g. "TEC" must not match "fintech", "URL" must not match "https://...", "UCA" != "education"
    for uni in config.CA_DR_UNIVERSITIES:
        if re.search(r"\b" + re.escape(uni.lower()) + r"\b", text):
            return True

    return False  # no signal found


def scan_tavily_queries(
    query_refinements: dict | None = None,
    extra_queries: list[str] | None = None,
) -> list[tuple[str, str]]:
    """
    Run TAVILY_MONITOR_QUERIES via Tavily search and return (text, source_tag) tuples.
    Used for JS-heavy sites (F6S, ProductHunt, Dealroom) plus dynamic disruption queries.

    query_refinements: optional dict mapping tag → extra search terms to append.
    extra_queries: optional list of additional query strings (e.g. from disruption research).
                   Auto-tagged as "tavily:extra:{i}". Capped at 8 to protect Tavily quota.
    Requires TAVILY_API_KEY. Silently skips if key is not set.
    """
    queries = list(config.TAVILY_MONITOR_QUERIES)
    tags = list(config.TAVILY_QUERY_TAGS)

    if extra_queries:
        for i, q in enumerate(extra_queries[:8]):
            queries.append(q)
            tags.append(f"tavily:extra:{i}")

    refinements = query_refinements or {}

    if not queries:
        return []

    tavily_key = config.get_optional_key("TAVILY_API_KEY")
    if not tavily_key:
        logger.info("No TAVILY_API_KEY — skipping Tavily monitor queries.")
        return []

    import requests as _requests

    TAVILY_SEARCH_URL = "https://api.tavily.com/search"
    results_out: list[tuple[str, str]] = []

    for i, query in enumerate(queries):
        tag = tags[i] if i < len(tags) else f"tavily:{i}"

        # Apply calibration refinements if present
        if tag in refinements:
            query = query + " " + refinements[tag]
            logger.info(f"  Applied calibration refinement to {tag}")

        logger.info(f"Tavily monitor query [{tag}]: {query[:60]}...")
        try:
            payload = {
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 10,
                "include_answer": False,
                "include_raw_content": False,
            }
            resp = _requests.post(
                TAVILY_SEARCH_URL, json=payload, timeout=config.REQUEST_TIMEOUT
            )
            if resp.status_code == 401:
                logger.warning("Tavily auth failed — check TAVILY_API_KEY.")
                break
            if resp.status_code == 429:
                logger.warning("Tavily rate limit hit — skipping remaining queries.")
                break
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                combined = " ".join(
                    f"{r.get('title', '')} {r.get('content', '')}"
                    for r in results
                )
                results_out.append((combined, tag))
                logger.info(f"  → {len(results)} result(s) returned")
            else:
                logger.info("  → No results")
        except Exception as exc:
            logger.warning(f"Tavily query failed: {exc}")

        time.sleep(config.REQUEST_DELAY)

    return results_out


def scan_firecrawl_sources() -> list[tuple[str, str]]:
    """
    Scrape JS-heavy accelerator/incubator pages via Firecrawl.
    Returns (markdown_text, source_tag) tuples for pages with content >= 200 chars.

    Requires FIRECRAWL_API_KEY and FIRECRAWL_ENABLED=True. Silently skips otherwise.
    Each URL is logged as a separate source for tracking.
    """
    if not config.FIRECRAWL_ENABLED:
        return []

    if not config.get_optional_key("FIRECRAWL_API_KEY"):
        logger.debug("No FIRECRAWL_API_KEY — skipping Firecrawl sources.")
        return []

    from tools.firecrawl_client import scrape_with_firecrawl

    results: list[tuple[str, str]] = []
    for url in config.FIRECRAWL_SOURCES:
        tag = config.FIRECRAWL_SOURCE_TAGS.get(url, f"firecrawl:{url.split('//')[-1].split('/')[0]}")
        logger.info(f"Firecrawl scrape: {url}")
        text = scrape_with_firecrawl(url)
        if len(text) >= 200:
            results.append((text, tag))
            logger.info(f"  → {len(text)} chars scraped from {tag}")
        else:
            logger.info(f"  → Insufficient content from {url} (got {len(text)} chars) — skipping")
        time.sleep(config.REQUEST_DELAY)

    return results


def scan_exa_queries() -> list[tuple[str, str]]:
    """
    Run EXA_MONITOR_QUERIES via Exa neural search.
    Returns (combined_text, source_tag) tuples, one per query.

    Requires EXA_API_KEY and EXA_ENABLED=True. Silently skips otherwise.
    Results pass through the same extract_company_names() + geo_prescreen()
    pipeline as Tavily results.
    """
    if not config.EXA_ENABLED:
        return []

    if not config.get_optional_key("EXA_API_KEY"):
        logger.debug("No EXA_API_KEY — skipping Exa queries.")
        return []

    from tools.exa_search import exa_search

    results: list[tuple[str, str]] = []
    for i, query in enumerate(config.EXA_MONITOR_QUERIES):
        tag = config.EXA_QUERY_TAGS[i] if i < len(config.EXA_QUERY_TAGS) else f"exa:{i}"
        logger.info(f"Exa query [{tag}]: {query[:60]}...")
        hits = exa_search(query, num_results=10)
        if hits:
            combined = " ".join(
                f"{r.get('title', '')} {r.get('text', '')}" for r in hits
            )
            results.append((combined, tag))
            logger.info(f"  → {len(hits)} result(s)")
        else:
            logger.info("  → No results")
        time.sleep(config.REQUEST_DELAY)

    return results


def scan_batches() -> list[tuple[str, str]]:
    """
    Scan all configured accelerator batch URLs for new company mentions.
    Returns a list of (text, source_tag) tuples for pages with new content.
    """
    urls = config.ACCELERATOR_BATCH_URLS
    if not urls:
        logger.info("No accelerator batch URLs configured — skipping.")
        return []

    cache = _load_cache()
    new_mentions: list[tuple[str, str]] = []

    for url in urls:
        source_tag = config.BATCH_URL_TAGS.get(url, url.split("//")[-1].split("/")[0])
        logger.info(f"Scanning batch page: {url}")
        text = _fetch_page_text(url)
        if not text:
            continue

        fp = _fingerprint(text)
        prev_fps = cache.get(url, [])

        if fp not in prev_fps:
            logger.info(f"  → New content detected at {url}")
            new_mentions.append((text, source_tag))
            cache[url] = [fp]
        else:
            logger.info(f"  → No changes at {url}")

        time.sleep(config.REQUEST_DELAY)

    _save_cache(cache)
    return new_mentions
