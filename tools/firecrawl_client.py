"""
tools/firecrawl_client.py — Firecrawl API client for Carica Scout.

Scrapes JS-rendered pages that BeautifulSoup cannot access.
Requires FIRECRAWL_API_KEY in .env (optional; pipeline works without it).

Validated sources in config.FIRECRAWL_SOURCES were tested against Firecrawl
before inclusion. senacyt.gob.pa was excluded because it uses CAPTCHA
challenges that Firecrawl cannot bypass.

Usage:
    from tools.firecrawl_client import scrape_with_firecrawl
    text = scrape_with_firecrawl("https://startuphonduras.com")
    # Returns markdown string or empty string on error/missing key
"""

from __future__ import annotations

import logging

import requests

import config
from tools.retry import with_retry

logger = logging.getLogger(__name__)

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"


@with_retry(max_attempts=2, base_delay=3.0, exceptions=(requests.RequestException,))
def _firecrawl_request(url: str, api_key: str) -> str:
    """POST to Firecrawl scrape endpoint and return markdown content."""
    payload = {
        "url": url,
        "formats": ["markdown"],
    }
    resp = requests.post(
        FIRECRAWL_SCRAPE_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,  # Firecrawl may take longer for JS-heavy pages
    )
    if resp.status_code == 401:
        logger.warning("Firecrawl auth failed — check FIRECRAWL_API_KEY.")
        return ""
    if resp.status_code == 429:
        logger.warning("Firecrawl rate limit hit — skipping.")
        return ""
    if resp.status_code not in (200, 201):
        logger.debug("Firecrawl returned %s for %s", resp.status_code, url)
        return ""
    data = resp.json()
    # Firecrawl returns {"success": true, "data": {"markdown": "..."}}
    return (data.get("data") or {}).get("markdown", "")


def scrape_with_firecrawl(url: str) -> str:
    """
    Scrape a JS-rendered page via Firecrawl.

    Returns clean markdown text, or empty string if:
    - FIRECRAWL_API_KEY is not set
    - FIRECRAWL_ENABLED is False
    - The page returns an error or empty content
    Never raises.
    """
    if not config.FIRECRAWL_ENABLED:
        return ""

    api_key = config.get_optional_key("FIRECRAWL_API_KEY")
    if not api_key:
        return ""

    try:
        return _firecrawl_request(url, api_key)
    except Exception as exc:
        logger.warning("Firecrawl scrape failed for %s: %s", url, exc)
        return ""
