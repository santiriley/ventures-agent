"""
monitor/events.py — Scan startup event calendars for CA/DR relevant events.

Used by scout.py (Workflow B — Weekly Monitor).
Pushes events to NOTION_DB_EVENTS if configured.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

# Regex to capture common date patterns in page text
_DATE_RE = re.compile(
    r"""
    (?:                             # Full month name or abbreviation
        (?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|
           Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)
        \s+\d{1,2}(?:,\s*\d{4})?   # e.g. "March 15, 2026"
    )|
    (?:\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})  # e.g. "03/15/2026" or "15-03-26"
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Keywords indicating startup / VC relevance
_STARTUP_KEYWORDS = [
    "startup", "emprendimiento", "venture", "pitch", "demo day",
    "accelerator", "incubador", "innovaci", "fintech", "techcrunch",
    "hackathon", "meetup", "founders", "inversión", "emprendedor",
]


@dataclass
class Event:
    title: str = ""
    date: str = ""
    location: str = ""
    url: str = ""
    source: str = ""
    notes: str = ""


def _fetch_soup(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.warning(f"Failed to fetch {url}: {exc}")
        return None


def _extract_events_from_page(soup: BeautifulSoup, source_url: str) -> list[Event]:
    """
    Extract event candidates from a parsed page.

    Strategy (in order):
    1. Look for <article>, <li>, <div> tags with common event class names
    2. Fall back to scanning anchor text for CA/DR keywords + date patterns
    """
    events: list[Event] = []
    ca_keywords = [c.lower() for c in config.CA_DR_COUNTRY_NAMES] + _STARTUP_KEYWORDS
    seen_titles: set[str] = set()

    # ── Pass 1: semantic event containers ──────────────────────────────────
    container_candidates = soup.find_all(
        ["article", "li", "div"],
        class_=re.compile(
            r"event|card|listing|item|post", re.IGNORECASE
        ),
        limit=60,
    )

    for tag in container_candidates:
        text = tag.get_text(separator=" ", strip=True)
        if not text or len(text) < 20:
            continue
        text_lower = text.lower()

        # Must mention at least one CA/DR or startup keyword
        if not any(kw in text_lower for kw in ca_keywords):
            continue

        title = text[:120].strip()
        if title in seen_titles:
            continue
        seen_titles.add(title)

        # Try to extract a date from the block text
        date_match = _DATE_RE.search(text)
        date_str = date_match.group(0) if date_match else ""

        # Try to find a link within the container
        anchor = tag.find("a", href=True)
        link = anchor["href"] if anchor else ""
        if link and link.startswith("/"):
            from urllib.parse import urljoin
            link = urljoin(source_url, link)

        events.append(Event(
            title=title,
            date=date_str,
            url=link,
            source=source_url,
        ))

    # ── Pass 2: fallback — scan all anchors ────────────────────────────────
    if not events:
        for anchor in soup.find_all("a", href=True, limit=200):
            text = anchor.get_text(strip=True)
            if not text or len(text) < 15:
                continue
            text_lower = text.lower()
            if not any(kw in text_lower for kw in ca_keywords):
                continue
            if text in seen_titles:
                continue
            seen_titles.add(text)

            href = anchor["href"]
            if href.startswith("/"):
                from urllib.parse import urljoin
                href = urljoin(source_url, href)

            date_match = _DATE_RE.search(text)
            events.append(Event(
                title=text[:120],
                date=date_match.group(0) if date_match else "",
                url=href,
                source=source_url,
            ))

    return events


def scan_events() -> list[Event]:
    """
    Scan configured event calendar URLs and return relevant events.
    """
    urls = config.EVENT_CALENDAR_URLS
    if not urls:
        logger.info("No event calendar URLs configured — skipping.")
        return []

    all_events: list[Event] = []

    for url in urls:
        logger.info(f"Scanning event calendar: {url}")
        soup = _fetch_soup(url)
        if soup is None:
            continue

        found = _extract_events_from_page(soup, url)
        logger.info(f"  → {len(found)} event candidate(s) at {url}")
        all_events.extend(found)

        time.sleep(config.REQUEST_DELAY)

    logger.info(f"Total events found: {len(all_events)}")
    return all_events


def push_events_to_notion(events: list[Event]) -> int:
    """
    Push events to NOTION_DB_EVENTS.
    Returns number of events pushed.
    """
    db_id = config.get_optional_key("NOTION_DB_EVENTS")
    if not db_id:
        logger.info("NOTION_DB_EVENTS not set — skipping event push.")
        return 0

    api_key = config.get_key("NOTION_API_KEY")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Notion-Version": config.NOTION_API_VERSION,
    }

    pushed = 0
    for event in events:
        properties: dict = {
            "Name": {"title": [{"text": {"content": event.title or "Untitled Event"}}]},
            "Location": {"rich_text": [{"text": {"content": event.location or ""}}]},
            "Source": {"rich_text": [{"text": {"content": event.source or ""}}]},
            "Notes": {"rich_text": [{"text": {"content": event.notes or ""}}]},
        }
        # Only include Date if we have one — Notion rejects empty date objects
        if event.date:
            properties["Date"] = {"date": {"start": event.date}}
        # Only include URL if non-empty
        if event.url:
            properties["URL"] = {"url": event.url}

        payload = {
            "parent": {"database_id": db_id},
            "properties": properties,
        }
        try:
            resp = requests.post(
                f"{config.NOTION_BASE_URL}/pages",
                headers=headers,
                json=payload,
                timeout=config.REQUEST_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                pushed += 1
            else:
                logger.warning(
                    f"Failed to push event '{event.title[:50]}': "
                    f"{resp.status_code} {resp.text[:120]}"
                )
        except Exception as exc:
            logger.warning(f"Failed to push event '{event.title[:50]}': {exc}")

    return pushed
