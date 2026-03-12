"""
tools/research.py — Web research for Carica Scout.

Primary:  Tavily API (AI-native search — structured, fast, relevant)
Fallback: requests + BeautifulSoup (if TAVILY_API_KEY not set)

Usage:
    from tools.research import research_company

    context = research_company("Paggo", "https://paggo.com")
    # Returns a string of enriched context to pass into enrich_with_claude()
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

import config
from tools.retry import with_retry

logger = logging.getLogger(__name__)


# ── Tavily ────────────────────────────────────────────────────────────────────

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


@with_retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _tavily_search(query: str, api_key: str) -> list[dict[str, Any]]:
    """Run a Tavily search and return list of result dicts."""
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": config.TAVILY_SEARCH_DEPTH,
        "max_results": config.TAVILY_MAX_RESULTS,
        "include_answer": True,
        "include_raw_content": False,
    }
    resp = requests.post(TAVILY_SEARCH_URL, json=payload, timeout=config.REQUEST_TIMEOUT)
    if resp.status_code == 401:
        logger.warning("Tavily auth failed — check TAVILY_API_KEY.")
        return []
    if resp.status_code == 429:
        logger.warning("Tavily rate limit hit — skipping search.")
        return []
    resp.raise_for_status()
    return resp.json().get("results", [])


@with_retry(max_attempts=2, base_delay=1.5, exceptions=(requests.RequestException,))
def _tavily_extract(url: str, api_key: str) -> str:
    """Extract clean content from a URL via Tavily."""
    payload = {"api_key": api_key, "urls": [url]}
    resp = requests.post(TAVILY_EXTRACT_URL, json=payload, timeout=config.REQUEST_TIMEOUT)
    if resp.status_code not in (200, 201):
        return ""
    results = resp.json().get("results", [])
    if results:
        return results[0].get("raw_content", "")[:4000]
    return ""


def _research_with_tavily(company_name: str, website: str | None, api_key: str) -> str:
    """
    Build an enriched context string using Tavily search + optional URL extraction.
    Returns a multi-paragraph string ready to append to the raw_input for Claude.
    """
    sections: list[str] = []

    # Search 1: General company info
    results = _tavily_search(
        f"{company_name} startup founders team funding", api_key
    )
    if results:
        snippets = [
            f"[{r.get('title', '')}] {r.get('content', '')}"
            for r in results[:3]
        ]
        sections.append("=== Web Search Results ===\n" + "\n\n".join(snippets))

    # Search 2: Funding / Crunchbase angle
    funding_results = _tavily_search(
        f"{company_name} funding round investment crunchbase linkedin", api_key
    )
    if funding_results:
        snippets = [
            f"[{r.get('title', '')}] {r.get('content', '')}"
            for r in funding_results[:2]
        ]
        sections.append("=== Funding Signals ===\n" + "\n\n".join(snippets))

    # URL extraction (company website)
    if website:
        extracted = _tavily_extract(website, api_key)
        if extracted:
            sections.append(f"=== Website Content ({website}) ===\n{extracted[:3000]}")

    # Search 3: LinkedIn founder/company profiles
    linkedin_results = _tavily_search(
        f"{company_name} founders site:linkedin.com", api_key
    )
    if linkedin_results:
        snippets = [
            f"[{r.get('title', '')}] {r.get('content', '')}"
            for r in linkedin_results[:2]
        ]
        sections.append("=== LinkedIn Signals ===\n" + "\n\n".join(snippets))

    # Search 4: GitHub — confirms tech foundation and founder activity
    github_results = _tavily_search(
        f"{company_name} founders site:github.com", api_key
    )
    if github_results:
        snippets = [
            f"[{r.get('title', '')}] {r.get('content', '')}"
            for r in github_results[:2]
        ]
        sections.append("=== GitHub Signals ===\n" + "\n\n".join(snippets))

    return "\n\n".join(sections)


# ── BeautifulSoup fallback ────────────────────────────────────────────────────

@with_retry(max_attempts=2, base_delay=1.5, exceptions=(requests.RequestException,))
def _scrape_url(url: str) -> str:
    """Fetch and extract clean text from a URL using BeautifulSoup."""
    resp = requests.get(
        url,
        timeout=config.REQUEST_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove nav/footer/script noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:5000]


def _research_with_bs(company_name: str, website: str | None) -> str:
    """
    Lightweight fallback using BeautifulSoup to scrape the company website.
    No search engine — only works if a URL is provided.
    """
    if not website:
        logger.debug(f"No website for {company_name} — fallback research skipped.")
        return ""

    try:
        text = _scrape_url(website)
        return f"=== Website Content ({website}) ===\n{text}"
    except Exception as exc:
        logger.warning(f"BeautifulSoup fallback failed for {website}: {exc}")
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def research_company(company_name: str, website: str | None = None) -> str:
    """
    Research a company and return an enriched context string.

    Uses Tavily if TAVILY_API_KEY is set; falls back to BeautifulSoup scraping.
    Returns empty string if no research can be performed (no key, no website).

    The returned string is designed to be appended to raw_input before
    passing to enrich_with_claude() for richer extraction.
    """
    tavily_key = config.get_optional_key("TAVILY_API_KEY")

    if tavily_key:
        logger.info(f"[research] Tavily search: {company_name}")
        context = _research_with_tavily(company_name, website, tavily_key)
    else:
        logger.info(f"[research] No TAVILY_API_KEY — BeautifulSoup fallback for {company_name}")
        context = _research_with_bs(company_name, website)

    time.sleep(config.REQUEST_DELAY)
    return context
