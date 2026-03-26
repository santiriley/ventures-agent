"""
tools/exa_search.py — Exa neural search client for Carica Scout.

Provides semantic search with better recall for niche CA/DR startup queries
than keyword-based search. Complements Tavily — different index, different results.

Requires EXA_API_KEY in .env (optional; pipeline works without it).

Usage:
    from tools.exa_search import exa_search
    results = exa_search("fintech startup Costa Rica pre-seed 2025", num_results=10)
    # Returns list of {title, url, text, published_date, score}
"""

from __future__ import annotations

import logging

import requests

import config
from tools.retry import with_retry

logger = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"


@with_retry(max_attempts=2, base_delay=2.0, exceptions=(requests.RequestException,))
def _exa_request(query: str, num_results: int, api_key: str) -> list[dict]:
    """POST to Exa search endpoint and return result list."""
    payload = {
        "query": query,
        "numResults": num_results,
        "contents": {
            "text": {"maxCharacters": 500},
        },
        "type": "neural",
    }
    resp = requests.post(
        EXA_SEARCH_URL,
        json=payload,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
        },
        timeout=config.REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        logger.warning("Exa auth failed — check EXA_API_KEY.")
        return []
    if resp.status_code == 429:
        logger.warning("Exa rate limit hit — skipping.")
        return []
    if resp.status_code not in (200, 201):
        logger.debug("Exa returned %s for query: %s", resp.status_code, query[:60])
        return []
    results = resp.json().get("results", [])
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "text": (r.get("text") or ""),
            "published_date": r.get("publishedDate", ""),
            "score": r.get("score", 0.0),
        }
        for r in results
    ]


def exa_search(query: str, num_results: int = 10) -> list[dict]:
    """
    Run a neural search via Exa.

    Returns list of {title, url, text, published_date, score}, or empty list if:
    - EXA_API_KEY is not set
    - EXA_ENABLED is False
    - Any API error occurs
    Never raises.
    """
    if not config.EXA_ENABLED:
        return []

    api_key = config.get_optional_key("EXA_API_KEY")
    if not api_key:
        return []

    try:
        return _exa_request(query, num_results, api_key)
    except Exception as exc:
        logger.warning("Exa search failed for '%s': %s", query[:60], exc)
        return []
