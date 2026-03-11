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
Exclude: the accelerator itself, VC funds, government programs, blogs, events, and non-companies.
Return ONLY a JSON array of company name strings. Example: ["Acme", "Betaworks", "Gamma"]
If no startups are found, return an empty array: []"""


def extract_company_names(page_text: str) -> list[str]:
    """
    Use Claude (fast model) to extract individual startup names from a batch page text.
    Returns a list of company name strings.
    """
    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
    try:
        message = client.messages.create(
            model=config.CLAUDE_MODEL_FAST,
            max_tokens=512,
            system=_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": page_text[:4000]}],
        )
        raw = message.content[0].text.strip()
        # Extract JSON array even if Claude adds surrounding text
        bracket_start = raw.find("[")
        bracket_end = raw.rfind("]")
        if bracket_start != -1 and bracket_end > bracket_start:
            raw = raw[bracket_start:bracket_end + 1]
        names = json.loads(raw)
        if isinstance(names, list):
            return [n for n in names if isinstance(n, str) and n.strip()]
    except Exception as exc:
        logger.warning(f"Company name extraction failed: {exc}")
    return []


def scan_batches() -> list[str]:
    """
    Scan all configured accelerator batch URLs for new company mentions.
    Returns a list of raw text snippets about new companies found since last run.
    """
    urls = config.ACCELERATOR_BATCH_URLS
    if not urls:
        logger.info("No accelerator batch URLs configured — skipping.")
        return []

    cache = _load_cache()
    new_mentions: list[str] = []

    for url in urls:
        logger.info(f"Scanning batch page: {url}")
        text = _fetch_page_text(url)
        if not text:
            continue

        fp = _fingerprint(text)
        prev_fps = cache.get(url, [])

        if fp not in prev_fps:
            logger.info(f"  → New content detected at {url}")
            new_mentions.append(text)
            cache[url] = [fp]  # store only latest fingerprint
        else:
            logger.info(f"  → No changes at {url}")

        time.sleep(config.REQUEST_DELAY)

    _save_cache(cache)
    return new_mentions
