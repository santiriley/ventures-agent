"""
monitor/batches.py — Scan accelerator batch pages for new CA/DR companies.

Used by scout.py (Workflow B — Weekly Monitor).
"""

from __future__ import annotations

import hashlib
import json
import logging
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


def geo_prescreen(name: str, snippet: str) -> bool:
    """
    Return True if at least 1 CA/DR geo signal is present in the combined name+snippet text.
    Deterministic — no API call. Uses full country names only (not 2-letter codes which
    cause false positives on common words like "payments" matching "PA" for Panama).
    """
    text = (name + " " + snippet).lower()
    # Use only full country names — 2-letter codes are too short for reliable text matching
    full_names = [c.lower() for c in config.TARGET_COUNTRIES.keys()]
    return any(country in text for country in full_names)


def scan_tavily_queries(query_refinements: dict | None = None) -> list[tuple[str, str]]:
    """
    Run TAVILY_MONITOR_QUERIES via Tavily search and return (text, source_tag) tuples.
    Used for JS-heavy sites (F6S, ProductHunt, Dealroom).

    query_refinements: optional dict mapping tag → extra search terms to append.
    Requires TAVILY_API_KEY. Silently skips if key is not set.
    """
    queries = config.TAVILY_MONITOR_QUERIES
    tags = config.TAVILY_QUERY_TAGS
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
