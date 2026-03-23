"""
notion/writer.py — Push enriched profiles to Notion with deduplication.

Primary function:
  push_lead(profile) → "created" | "duplicate" | "portfolio" | raises on error
"""

from __future__ import annotations

import datetime
import logging

import requests

import config
from config import OVER_STAGE_VALUES
from enrichment.engine import CompanyProfile

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.get_key('NOTION_API_KEY')}",
        "Content-Type": "application/json",
        "Notion-Version": config.NOTION_API_VERSION,
    }


def _normalize_name(name: str) -> str:
    """Lowercase, strip whitespace, and strip common legal suffixes for dedup comparison."""
    n = name.casefold().strip()
    for suffix in config.LEGAL_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
            break  # only strip one suffix
    return n


def _search_existing(name: str) -> str | None:
    """
    Return Notion page ID if a lead with this company name already exists.
    Checks both exact match and normalized (case-insensitive, suffix-stripped) match.
    """
    url = f"{config.NOTION_BASE_URL}/databases/{config.get_key('NOTION_DB_LEADS')}/query"
    # Notion title filter is case-sensitive; we fetch all pages with a broad contains
    # filter and normalize client-side to catch "Acme Inc" vs "acme" vs "ACME".
    payload = {
        "filter": {
            "property": "Name",
            "title": {"contains": _normalize_name(name).split()[0] if name.strip() else name},
        },
        "page_size": 50,
    }
    resp = requests.post(url, headers=_headers(), json=payload, timeout=config.REQUEST_TIMEOUT)
    if resp.status_code == 401:
        raise EnvironmentError("Notion API auth failed — check NOTION_API_KEY.")
    if resp.status_code == 400:
        raise ValueError(f"Notion schema error: {resp.text}")
    resp.raise_for_status()

    needle = _normalize_name(name)
    for page in resp.json().get("results", []):
        title_parts = page.get("properties", {}).get("Name", {}).get("title", [])
        existing_name = "".join(t.get("plain_text", "") for t in title_parts)
        if _normalize_name(existing_name) == needle:
            return page["id"]
    return None


def _is_portfolio(name: str) -> bool:
    return _normalize_name(name) in config.PORTFOLIO_COMPANIES


# Funding signals that suggest a stage='unknown' company is actually late-stage.
# These are checked against notes + one_liner to catch companies Claude couldn't stage.
_LARGE_RAISE_SIGNALS = [
    "$5m", "$10m", "$15m", "$20m", "$50m", "$100m",
    "series b", "series c", "series d", "growth round",
]


def _is_too_late_stage(profile: CompanyProfile) -> bool:
    """Return True if the company's extracted stage is outside the fund mandate."""
    return profile.stage.lower() in OVER_STAGE_VALUES


def _is_unknown_stage_but_overfunded(profile: CompanyProfile) -> bool:
    """
    Return True if stage is 'unknown' but notes/one_liner contain large funding signals.
    Catches companies where Claude couldn't determine stage but funding data is clearly late-stage.
    """
    if profile.stage.lower() != "unknown":
        return False
    text = (profile.notes + " " + profile.one_liner).lower()
    return any(sig in text for sig in _LARGE_RAISE_SIGNALS)


def _build_page_properties(profile: CompanyProfile) -> dict:
    """Build Notion page properties from a CompanyProfile."""
    founders_text = "; ".join(
        f"{f.name} [{f.geo_score} geo signals]" for f in profile.founders
    ) if profile.founders else "Unknown"

    return {
        "Name": {
            "title": [{"text": {"content": profile.name or "Unknown"}}]
        },
        "Website": {
            "url": profile.website or None
        },
        "One-liner": {
            "rich_text": [{"text": {"content": profile.one_liner or ""}}]
        },
        "Sector": {
            "select": {"name": profile.sector} if profile.sector else None
        },
        "Stage": {
            "select": {"name": profile.stage.title()} if profile.stage else None
        },
        "Country": {
            "select": {"name": profile.country} if profile.country else None
        },
        "Founders": {
            "rich_text": [{"text": {"content": founders_text}}]
        },
        "Thesis Score": {
            "number": profile.thesis.score if profile.thesis else None
        },
        "Thesis Rationale": {
            "rich_text": [{"text": {"content": profile.thesis.rationale if profile.thesis else ""}}]
        },
        "Contact Email": {
            "email": profile.contact.email if profile.contact and profile.contact.email else None
        },
        "Contact Confidence": {
            "select": {"name": profile.contact.confidence} if profile.contact else None
        },
        "Source": {
            "rich_text": [{"text": {"content": profile.source or ""}}]
        },
        "Date Found": {
            "date": {"start": profile.date_found or datetime.date.today().isoformat()}
        },
        "Status": {
            "select": {"name": "New 🆕"}
        },
        "Notes": {
            "rich_text": [{"text": {"content": profile.notes or ""}}]
        },
    }


# ── Main push function ───────────────────────────────────────────────────────

def push_lead(profile: CompanyProfile) -> str:
    """
    Push an enriched CompanyProfile to the Notion leads database.

    Returns:
      "created"   — new page created, Status = "New 🆕"
      "duplicate" — company already exists, skipped silently
      "portfolio"      — known portfolio company, skipped silently
      "stage_blocked"  — Series B+ or unknown-stage with large funding signals

    Raises:
      EnvironmentError — on auth failure (stops the run)
      ValueError        — on schema mismatch (stops the run, reports field)
    """
    if not profile.name:
        logger.warning("push_lead called with empty company name — skipping.")
        return "duplicate"

    # Portfolio check
    if _is_portfolio(profile.name):
        logger.info(f"[SKIP portfolio] {profile.name}")
        return "portfolio"

    # Stage gate — block Series B+ and unknown-stage companies with large funding signals
    if _is_too_late_stage(profile) or _is_unknown_stage_but_overfunded(profile):
        logger.info(f"[SKIP stage] {profile.name} — stage '{profile.stage}' outside fund mandate")
        return "stage_blocked"

    # Duplicate check
    existing_id = _search_existing(profile.name)
    if existing_id:
        logger.info(f"[SKIP duplicate] {profile.name} already in Notion ({existing_id})")
        return "duplicate"

    # Build page
    db_id = config.get_key("NOTION_DB_LEADS")
    properties = _build_page_properties(profile)

    # Remove None selects (Notion rejects null select values)
    for key, val in list(properties.items()):
        if isinstance(val, dict):
            if val.get("select") is None and "select" in val:
                del properties[key]
            if val.get("url") is None and "url" in val:
                properties[key] = {"url": None}

    payload = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }

    resp = requests.post(
        f"{config.NOTION_BASE_URL}/pages",
        headers=_headers(),
        json=payload,
        timeout=config.REQUEST_TIMEOUT,
    )

    if resp.status_code == 401:
        raise EnvironmentError("Notion API auth failed — check NOTION_API_KEY.")

    if resp.status_code == 400:
        error_msg = resp.json().get("message", resp.text)
        raise ValueError(
            f"Notion schema mismatch — stop and fix before continuing.\n"
            f"Error: {error_msg}\n"
            f"Company: {profile.name}"
        )

    resp.raise_for_status()

    page_id = resp.json().get("id", "")
    logger.info(f"[CREATED] {profile.name} → Notion page {page_id}")
    return "created"
