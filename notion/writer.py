"""
notion/writer.py — Push enriched profiles to Notion with deduplication.

Primary function:
  push_lead(profile) → "created" | "duplicate" | "portfolio" | raises on error
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import replace as _dc_replace

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


def _search_existing_by_founders(founder_urls: list[str]) -> list[dict] | None:
    """
    Search for existing Notion leads that share a founder LinkedIn URL.

    Runs one query per URL (Notion API does not support 'contains any of' natively).
    Returns list of {name, status, page_id} dicts, or None if no matches found.
    Logs a warning if len(founder_urls) > 4 (unusual; still runs but may be slow).
    """
    if not founder_urls:
        return None

    if len(founder_urls) > 4:
        logger.warning(
            f"_search_existing_by_founders called with {len(founder_urls)} URLs — "
            "this is unusual; running {len(founder_urls)} Notion queries."
        )

    db_id = config.get_key("NOTION_DB_LEADS")
    url = f"{config.NOTION_BASE_URL}/databases/{db_id}/query"
    found: dict[str, dict] = {}  # page_id → result, dedup across queries

    for linkedin_url in founder_urls:
        if not linkedin_url:
            continue
        payload = {
            "filter": {
                "property": "Founder LinkedIn",
                "rich_text": {"contains": linkedin_url},
            },
            "page_size": 10,
        }
        try:
            resp = requests.post(url, headers=_headers(), json=payload, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 401:
                raise EnvironmentError("Notion API auth failed — check NOTION_API_KEY.")
            if resp.status_code in (400, 404):
                # Property doesn't exist yet — gracefully skip founder dedup
                logger.debug(
                    "Founder LinkedIn property not found in Notion schema — "
                    "skipping founder dedup (add property per NOTION_SETUP.md)"
                )
                return None
            resp.raise_for_status()
            for page in resp.json().get("results", []):
                page_id = page["id"]
                if page_id in found:
                    continue
                title_parts = page.get("properties", {}).get("Name", {}).get("title", [])
                name = "".join(t.get("plain_text", "") for t in title_parts)
                status_parts = page.get("properties", {}).get("Status", {}).get("select") or {}
                status = status_parts.get("name", "")
                found[page_id] = {"name": name, "status": status, "page_id": page_id}
        except EnvironmentError:
            raise
        except Exception as exc:
            logger.debug("Founder dedup query failed for %s: %s", linkedin_url, exc)

    return list(found.values()) if found else None


def _is_portfolio(name: str) -> bool:
    return _normalize_name(name) in config.PORTFOLIO_COMPANIES


def already_in_notion(name: str) -> bool:
    """
    Lightweight pre-enrichment check. Returns True if normalized name already
    exists in NOTION_DB_LEADS. Makes 1 Notion API call; safe to call per candidate.
    """
    return _search_existing(name) is not None


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
        "Portfolio Fit Score": {
            "number": profile.portfolio_fit_score
        },
        "Portfolio Fit Note": {
            "rich_text": [{"text": {"content": profile.portfolio_fit_note or ""}}]
        },
        "Traction Signals": {
            "rich_text": [{"text": {"content": "; ".join(profile.traction_signals) or ""}}]
        },
        "Founder Background": {
            "rich_text": [{"text": {"content": profile.founder_relevance_note or ""}}]
        },
        "Non-CA Founder (Building in Region)": {
            "checkbox": profile.non_ca_founder_building_in_region
        },
        "Founder LinkedIn": {
            "rich_text": [{"text": {"content": ", ".join(profile.founder_linkedin_urls)}}]
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

    # Duplicate check — by company name
    existing_id = _search_existing(profile.name)
    if existing_id:
        logger.info(f"[SKIP duplicate] {profile.name} already in Notion ({existing_id})")
        return "duplicate"

    # Founder-level dedup — catches same founder at a new company
    founder_matches = _search_existing_by_founders(
        getattr(profile, "founder_linkedin_urls", [])
    )
    if founder_matches:
        for match in founder_matches:
            old_company = match["name"]
            status = match["status"]
            if "Portfolio" in status:
                logger.info(
                    f"[SKIP portfolio founder] {profile.name} — founder previously at "
                    f"portfolio company '{old_company}'"
                )
                return "portfolio"
            elif "Passed" in status:
                # Founder was passed on — still push but flag for re-evaluation
                logger.info(
                    f"[FOUNDER REENGAGEMENT] {profile.name} — founder previously seen at "
                    f"'{old_company}' (status: {status}). Pushing with re-evaluation note."
                )
                note = f"⚠️ Founder previously seen at {old_company} (status: {status}). Re-evaluate."
                updated_notes = (note + "\n" + profile.notes).strip() if profile.notes else note
                profile = _dc_replace(profile, notes=updated_notes)
            else:
                # Founder is active in another pipeline entry — skip to avoid duplicate diligence
                logger.info(
                    f"[SKIP founder in pipeline] {profile.name} — founder already in "
                    f"pipeline at '{old_company}' (status: {status})"
                )
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


# ── Disruption Research push ─────────────────────────────────────────────────

def push_disruption_memo(theme: dict, run_date: str) -> str:
    """
    Push one structured disruption sector theme to NOTION_DB_DISRUPTION.

    Deduplicates by sector + quarter — if a page for this sector already exists
    in the same quarter, it is updated in place rather than duplicated.

    Returns "created" | "updated" | "skipped" (no DB configured) | "failed"

    Required Notion DB schema (NOTION_SETUP.md §4):
      Name                  (title)
      Sector                (select)
      Date                  (date)
      Refresh Due           (date)
      Incumbents Disrupted  (rich_text)
      Disruption Pattern    (select)
      Why Now               (rich_text)
      Key Evidence          (rich_text)
      Counterargument       (rich_text)
      CA/DR Angle           (rich_text)
      Companies Spotted     (rich_text)
      Next Research         (rich_text)
      Confidence            (select — Strong signal | Emerging | Speculative)
      Queries Run           (rich_text)
      Type                  (select — must have "Sector Memo" option)
    """
    db_id = config.get_optional_key("NOTION_DB_DISRUPTION")
    if not db_id:
        logger.debug("NOTION_DB_DISRUPTION not set — skipping disruption theme push.")
        return "skipped"

    sector = (theme.get("sector") or "Unknown").strip()
    # Quarter label: e.g. "Q1 2026"
    year = run_date[:4]
    month = int(run_date[5:7])
    quarter = f"Q{(month - 1) // 3 + 1} {year}"
    page_name = f"{sector} — {quarter}"

    # ── Dedup: search for existing page this quarter ──────────────────────────
    existing_id = _search_disruption_page(db_id, page_name)

    # ── Build properties ──────────────────────────────────────────────────────
    key_evidence = "\n".join(f"• {e}" for e in (theme.get("key_evidence") or []))
    next_research = "\n".join(f"• {q}" for q in (theme.get("next_research") or []))
    companies_spotted = ", ".join(theme.get("companies_spotted") or [])

    # Map confidence values to Notion select labels
    _confidence_map = {
        "strong_signal": "Strong signal",
        "emerging": "Emerging",
        "speculative": "Speculative",
    }
    confidence_label = _confidence_map.get(
        (theme.get("confidence") or "").lower(), "Emerging"
    )

    # Refresh due = 90 days from run date
    try:
        refresh_due = (
            datetime.date.fromisoformat(run_date) + datetime.timedelta(days=90)
        ).isoformat()
    except ValueError:
        refresh_due = run_date

    properties = {
        "Name": {
            "title": [{"text": {"content": page_name}}]
        },
        "Sector": {
            "select": {"name": sector}
        },
        "Date": {
            "date": {"start": run_date}
        },
        "Refresh Due": {
            "date": {"start": refresh_due}
        },
        "Incumbents Disrupted": {
            "rich_text": [{"text": {"content": (theme.get("incumbents_disrupted") or "")[:2000]}}]
        },
        "Disruption Pattern": {
            "select": {"name": theme.get("disruption_pattern") or "New category"}
        },
        "Why Now": {
            "rich_text": [{"text": {"content": (theme.get("why_now") or "")[:2000]}}]
        },
        "Key Evidence": {
            "rich_text": [{"text": {"content": key_evidence[:2000]}}]
        },
        "Counterargument": {
            "rich_text": [{"text": {"content": (theme.get("counterargument") or "")[:2000]}}]
        },
        "CA/DR Angle": {
            "rich_text": [{"text": {"content": (theme.get("ca_dr_angle") or "")[:2000]}}]
        },
        "Companies Spotted": {
            "rich_text": [{"text": {"content": companies_spotted[:2000]}}]
        },
        "Next Research": {
            "rich_text": [{"text": {"content": next_research[:2000]}}]
        },
        "Confidence": {
            "select": {"name": confidence_label}
        },
        "Type": {
            "select": {"name": "Sector Memo"}
        },
    }

    try:
        if existing_id:
            # Update existing page
            resp = requests.patch(
                f"{config.NOTION_BASE_URL}/pages/{existing_id}",
                headers=_headers(),
                json={"properties": properties},
                timeout=config.REQUEST_TIMEOUT,
            )
        else:
            # Create new page
            resp = requests.post(
                f"{config.NOTION_BASE_URL}/pages",
                headers=_headers(),
                json={"parent": {"database_id": db_id}, "properties": properties},
                timeout=config.REQUEST_TIMEOUT,
            )

        if resp.status_code == 401:
            raise EnvironmentError("Notion API auth failed — check NOTION_API_KEY.")

        if resp.status_code == 400:
            error_msg = resp.json().get("message", resp.text)
            logger.warning(
                f"Disruption memo schema mismatch for '{page_name}': {error_msg}. "
                f"Check NOTION_SETUP.md §4 for required property names."
            )
            return "failed"

        resp.raise_for_status()
        action = "updated" if existing_id else "created"
        logger.info(f"[{action.upper()}] Disruption theme '{page_name}' → Notion")
        return action

    except EnvironmentError:
        raise
    except Exception as exc:
        logger.warning(f"Disruption memo push failed for '{page_name}': {exc}")
        return "failed"


def _search_disruption_page(db_id: str, page_name: str) -> str | None:
    """Return page ID if a disruption research page with this name already exists."""
    url = f"{config.NOTION_BASE_URL}/databases/{db_id}/query"
    payload = {
        "filter": {
            "property": "Name",
            "title": {"equals": page_name},
        },
        "page_size": 1,
    }
    try:
        resp = requests.post(url, headers=_headers(), json=payload, timeout=config.REQUEST_TIMEOUT)
        if resp.status_code in (400, 401):
            return None
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None
    except Exception:
        return None


# ── Market intel push ────────────────────────────────────────────────────────

def push_market_intel(memo_text: str, run_date: str, queries: list[str]) -> str:
    """
    Push a disruption intelligence memo to NOTION_DB_MARKET_INTEL.

    Returns "created" | "skipped".

    Skips silently if NOTION_DB_MARKET_INTEL is not configured — the local
    .tmp/disruption_memo_{date}.md file is sufficient as an artifact.

    Does NOT fall back to NOTION_DB_EVENTS (incompatible schema).

    Required Notion DB schema:
      Name       (title)
      Date       (date)
      Memo       (rich_text)
      Queries Run (rich_text)
      Type       (select — must have "Market Intel" option)
    """
    db_id = config.get_optional_key("NOTION_DB_MARKET_INTEL")
    if not db_id:
        logger.info("NOTION_DB_MARKET_INTEL not set — disruption memo saved locally only.")
        return "skipped"

    properties = {
        "Name": {
            "title": [{"text": {"content": f"Disruption Intel — {run_date}"}}]
        },
        "Date": {
            "date": {"start": run_date}
        },
        "Memo": {
            "rich_text": [{"text": {"content": memo_text[:2000]}}]
        },
        "Queries Run": {
            "rich_text": [{"text": {"content": "\n".join(queries)}}]
        },
        "Type": {
            "select": {"name": "Market Intel"}
        },
    }

    payload = {"parent": {"database_id": db_id}, "properties": properties}
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
            f"Notion schema mismatch for market intel push.\n"
            f"Error: {error_msg}\n"
            f"Ensure NOTION_DB_MARKET_INTEL has: Name (title), Date, Memo, "
            f"Queries Run (rich_text), Type (select with 'Market Intel' option)"
        )

    resp.raise_for_status()
    page_id = resp.json().get("id", "")
    logger.info(f"[CREATED] Disruption memo — {run_date} → Notion page {page_id}")
    return "created"
