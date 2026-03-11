"""
monitor/network.py — Scan portfolio founder networks for new company mentions.

Used by scout.py (Workflow B — Weekly Monitor).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import anthropic
import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

CACHE_FILE = config.TMP_DIR / "network_cache.json"

FILTER_PROMPT = """You are a startup deal sourcing assistant for Carica VC.

Below is raw text scraped from a founder network page or LinkedIn-like profile.
Your job: identify any mentions of NEW startups or companies that are NOT already
in Carica's portfolio (portfolio list will be given).

Portfolio to exclude: {portfolio}

Return a JSON list of company mentions like:
[
  {{"name": "Company Name", "snippet": "brief context from the page"}}
]

Return an empty list [] if no new companies are found.
Only return valid JSON, nothing else.

TEXT:
{text}
"""


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _fetch_text(url: str) -> str:
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup.get_text(separator=" ", strip=True)[:8000]  # cap to avoid token overrun
    except Exception as exc:
        logger.warning(f"Failed to fetch {url}: {exc}")
        return ""


def _filter_with_claude(text: str) -> list[dict]:
    """Use Claude (fast/cheap model) to extract company mentions from page text."""
    if not text.strip():
        return []

    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
    portfolio_str = ", ".join(sorted(config.PORTFOLIO_COMPANIES))

    message = client.messages.create(
        model=config.CLAUDE_MODEL_FAST,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": FILTER_PROMPT.format(portfolio=portfolio_str, text=text),
        }],
    )

    raw = message.content[0].text.strip()
    try:
        return json.loads(raw)
    except Exception:
        return []


def scan_network() -> list[dict]:
    """
    Scan configured network profile URLs and return new company mentions.
    Returns list of dicts: [{name, snippet}]
    """
    urls = config.NETWORK_PROFILE_URLS
    if not urls:
        logger.info("No network profile URLs configured — skipping.")
        return []

    mentions: list[dict] = []

    for url in urls:
        logger.info(f"Scanning network page: {url}")
        text = _fetch_text(url)
        if not text:
            continue

        found = _filter_with_claude(text)
        logger.info(f"  → {len(found)} new mention(s) at {url}")
        mentions.extend(found)

        time.sleep(config.REQUEST_DELAY)

    return mentions
