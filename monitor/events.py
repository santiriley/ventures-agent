"""
monitor/events.py — Scan startup event calendars for CA/DR relevant events.

Used by scout.py (Workflow B — Weekly Monitor).
Pushes events to NOTION_DB_EVENTS if configured.

Discovery strategy:
  1. HTML scrape: static event calendar pages (fast, free)
  2. Tavily semantic search: handles JS-rendered pages the HTML scraper cannot reach
Events from both sources are deduplicated by URL, filtered to future-only, then pushed to Notion.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import time
from dataclasses import dataclass

import anthropic
import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

# ── Date parsing helpers ─────────────────────────────────────────────────────

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

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# ── Keywords indicating startup / VC relevance ───────────────────────────────

_STARTUP_KEYWORDS = [
    "startup", "emprendimiento", "venture", "pitch", "demo day",
    "accelerator", "incubador", "innovaci", "fintech", "techcrunch",
    "hackathon", "meetup", "founders", "inversión", "emprendedor",
]

# ── Claude prompt for Tavily-sourced event extraction ───────────────────────

_EVENT_EXTRACT_PROMPT = """You are extracting startup ecosystem events from web search results.

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "events": [
    {
      "title": "Event full name",
      "date": "YYYY-MM-DD or free text like 'April 2026' or 'Q2 2026'",
      "location": "City, Country or 'Virtual'",
      "url": "direct event URL if clearly present in the results",
      "notes": "one sentence: what the event is and who it is for"
    }
  ]
}

Rules:
- Only include events relevant to the startup/tech/VC ecosystem in Central America or the Dominican Republic
- Only include UPCOMING events — skip past events and event recaps
- Skip funding announcements, news articles, company profiles, and directory listings
- Maximum 5 events per response
- Return {"events": []} if nothing clearly qualifies"""


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Event:
    title: str = ""
    date: str = ""
    location: str = ""
    url: str = ""
    source: str = ""
    notes: str = ""


# ── Date filter ──────────────────────────────────────────────────────────────

def _is_future_event(date_str: str) -> bool:
    """
    Return True if the event is upcoming (or if the date cannot be parsed — conservative).
    Events more than 7 days in the past are dropped.
    """
    if not date_str:
        return True
    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=7)
    date_lower = date_str.lower().strip()

    # ISO format: YYYY-MM-DD
    try:
        return datetime.date.fromisoformat(date_str[:10]) >= cutoff
    except ValueError:
        pass

    # "Month YYYY" (year-only, e.g. "April 2026")
    m = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{4})\b",
        date_lower,
    )
    if m:
        return int(m.group(2)) >= today.year

    # "Month DD, YYYY" or "Month DD YYYY" (e.g. "March 15, 2026")
    m = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})(?!\d)(?:,?\s*(\d{4}))?",
        date_lower,
    )
    if m:
        month = _MONTH_MAP.get(m.group(1)[:3], 0)
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            return datetime.date(year, month, day) >= cutoff
        except ValueError:
            pass

    # MM/DD/YYYY or DD-MM-YY
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})", date_str)
    if m:
        try:
            year = int(m.group(3))
            if year < 100:
                year += 2000
            return datetime.date(year, int(m.group(1)), int(m.group(2))) >= cutoff
        except ValueError:
            pass

    # Year-only: keep if year is current or future
    m = re.search(r"\b(20\d{2})\b", date_str)
    if m:
        return int(m.group(1)) >= today.year

    # Unparseable — keep it (conservative)
    return True


# ── HTML scraper ─────────────────────────────────────────────────────────────

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
    Extract event candidates from a parsed static HTML page.

    Strategy:
    1. Look for <article>, <li>, <div> tags with common event class names
    2. Fall back to scanning anchor text for CA/DR keywords + date patterns
    """
    events: list[Event] = []
    ca_keywords = [c.lower() for c in config.CA_DR_COUNTRY_NAMES] + _STARTUP_KEYWORDS
    seen_titles: set[str] = set()

    container_candidates = soup.find_all(
        ["article", "li", "div"],
        class_=re.compile(r"event|card|listing|item|post", re.IGNORECASE),
        limit=60,
    )

    for tag in container_candidates:
        text = tag.get_text(separator=" ", strip=True)
        if not text or len(text) < 20:
            continue
        if not any(kw in text.lower() for kw in ca_keywords):
            continue
        title = text[:120].strip()
        if title in seen_titles:
            continue
        seen_titles.add(title)

        date_match = _DATE_RE.search(text)
        anchor = tag.find("a", href=True)
        link = anchor["href"] if anchor else ""
        if link and link.startswith("/"):
            from urllib.parse import urljoin
            link = urljoin(source_url, link)

        events.append(Event(
            title=title,
            date=date_match.group(0) if date_match else "",
            url=link,
            source=source_url,
        ))

    if not events:
        for anchor in soup.find_all("a", href=True, limit=200):
            text = anchor.get_text(strip=True)
            if not text or len(text) < 15:
                continue
            if not any(kw in text.lower() for kw in ca_keywords):
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


# ── Tavily event scanner ──────────────────────────────────────────────────────

def _scan_tavily_events() -> list[Event]:
    """
    Use Tavily semantic search + Claude Haiku to discover upcoming CA/DR startup events.
    Handles JS-rendered pages and aggregator sites that BeautifulSoup cannot parse.
    Returns [] if TAVILY_API_KEY is not set.
    """
    tavily_key = config.get_optional_key("TAVILY_API_KEY")
    if not tavily_key:
        logger.info("No TAVILY_API_KEY — skipping Tavily event queries.")
        return []

    queries: list[tuple[str, str]] = getattr(config, "EVENT_TAVILY_QUERIES", [])
    if not queries:
        return []

    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
    all_events: list[Event] = []

    for query, tag in queries:
        logger.info(f"Tavily event query [{tag}]: {query[:60]}...")
        try:
            payload = {
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 10,
                "include_answer": False,
                "include_raw_content": False,
            }
            resp = requests.post(
                "https://api.tavily.com/search", json=payload, timeout=config.REQUEST_TIMEOUT
            )
            if resp.status_code == 401:
                logger.warning("Tavily auth failed — check TAVILY_API_KEY.")
                break
            if resp.status_code == 429:
                logger.warning("Tavily rate limit — skipping remaining event queries.")
                break
            if resp.status_code != 200:
                logger.warning(f"  Tavily returned {resp.status_code} for [{tag}] — skipping.")
                time.sleep(config.REQUEST_DELAY)
                continue

            results = resp.json().get("results", [])
            logger.info(f"  → {len(results)} search result(s)")
            if not results:
                time.sleep(config.REQUEST_DELAY)
                continue

            combined = "\n\n".join(
                f"Title: {r.get('title', '')}\nURL: {r.get('url', '')}\nContent: {r.get('content', '')[:400]}"
                for r in results
            )

            try:
                message = client.messages.create(
                    model=config.CLAUDE_MODEL_FAST,
                    max_tokens=1024,
                    system=_EVENT_EXTRACT_PROMPT,
                    messages=[{"role": "user", "content": combined}],
                )
                raw = message.content[0].text.strip()
                # Strip markdown code fences if present
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                extracted = json.loads(raw).get("events", [])
                count = 0
                for ev in extracted:
                    title = (ev.get("title") or "").strip()
                    if not title:
                        continue
                    all_events.append(Event(
                        title=title,
                        date=ev.get("date") or "",
                        location=ev.get("location") or "",
                        url=ev.get("url") or "",
                        source=tag,
                        notes=ev.get("notes") or "",
                    ))
                    count += 1
                logger.info(f"  → {count} event(s) extracted")
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning(f"  Event extraction failed for [{tag}]: {exc}")

            time.sleep(config.REQUEST_DELAY)

        except Exception as exc:
            logger.warning(f"  Tavily event query [{tag}] failed: {exc}")

    return all_events


# ── Main scan entry point ─────────────────────────────────────────────────────

def scan_events() -> list[Event]:
    """
    Discover upcoming CA/DR startup events from HTML calendars and Tavily search.
    Deduplicates by URL and filters to future events only.
    """
    seen_urls: set[str] = set()
    all_events: list[Event] = []

    # ── Pass 1: static HTML calendars ────────────────────────────────────────
    html_count = 0
    for url in config.EVENT_CALENDAR_URLS:
        logger.info(f"Scanning event calendar: {url}")
        soup = _fetch_soup(url)
        if soup is None:
            continue
        found = _extract_events_from_page(soup, url)
        logger.info(f"  → {len(found)} event candidate(s) at {url}")
        for ev in found:
            if ev.url and ev.url in seen_urls:
                continue
            if ev.url:
                seen_urls.add(ev.url)
            all_events.append(ev)
            html_count += 1
        time.sleep(config.REQUEST_DELAY)

    # ── Pass 2: Tavily semantic search ────────────────────────────────────────
    tavily_events = _scan_tavily_events()
    tavily_count = 0
    dedup_count = 0
    for ev in tavily_events:
        if ev.url and ev.url in seen_urls:
            dedup_count += 1
            continue
        if ev.url:
            seen_urls.add(ev.url)
        all_events.append(ev)
        tavily_count += 1

    # ── Filter: drop events clearly in the past ───────────────────────────────
    future_events = [ev for ev in all_events if _is_future_event(ev.date)]
    dropped = len(all_events) - len(future_events)

    logger.info(
        f"Events: {html_count} HTML + {tavily_count} Tavily"
        + (f", {dedup_count} deduplicated" if dedup_count else "")
        + (f", {dropped} past filtered" if dropped else "")
        + f" → {len(future_events)} total"
    )
    return future_events


# ── Notion push ───────────────────────────────────────────────────────────────

def push_events_to_notion(events: list[Event]) -> int:
    """
    Push events to NOTION_DB_EVENTS.
    Returns number of events pushed.
    """
    db_id = config.get_optional_key("NOTION_DB_EVENTS")
    if not db_id:
        logger.info("NOTION_DB_EVENTS not set — skipping event push.")
        return 0

    if not events:
        logger.info("  0 future events — nothing to push.")
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
        if event.date:
            properties["Date"] = {"date": {"start": event.date}}
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
