"""
tools/linkedin.py — Proxycurl LinkedIn profile enrichment for Carica Scout.

Fetches structured LinkedIn data to improve geo signal accuracy.
Requires PROXYCURL_API_KEY in .env (optional; pipeline works without it).

Usage:
    from tools.linkedin import fetch_linkedin_profile
    data = fetch_linkedin_profile("https://linkedin.com/in/handle")
    # Returns dict or None
"""

from __future__ import annotations

import logging
import time

import requests

import config
from tools.retry import with_retry

logger = logging.getLogger(__name__)

PROXYCURL_URL = "https://nubela.co/proxycurl/api/v2/linkedin"

# Rate limit: Proxycurl allows 1 req/second on the free tier
_REQUEST_DELAY = 1.1


@with_retry(max_attempts=3, base_delay=2.0, exceptions=(requests.RequestException,))
def _call_proxycurl(linkedin_url: str, api_key: str) -> dict | None:
    """Call the Proxycurl API and return raw response data."""
    resp = requests.get(
        PROXYCURL_URL,
        params={"url": linkedin_url},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=config.REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        logger.warning("Proxycurl auth failed — check PROXYCURL_API_KEY.")
        return None
    if resp.status_code == 429:
        logger.warning("Proxycurl rate limit hit — skipping.")
        return None
    if resp.status_code == 404:
        logger.debug("Proxycurl: LinkedIn profile not found: %s", linkedin_url)
        return None
    if resp.status_code == 422:
        logger.debug("Proxycurl: invalid URL: %s", linkedin_url)
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_linkedin_profile(linkedin_url: str) -> dict | None:
    """
    Fetch structured LinkedIn profile data via Proxycurl.

    Returns a dict with:
        full_name, headline, city, country_full_name,
        education: [{school, degree, field_of_study, starts_at, ends_at}],
        experiences: [{company, title, location, starts_at, ends_at}]

    Returns None if PROXYCURL_API_KEY is not set, the profile is not found,
    or any API error occurs. Never raises — caller should handle None.
    """
    if not linkedin_url or "linkedin.com" not in linkedin_url.lower():
        return None

    api_key = config.get_optional_key("PROXYCURL_API_KEY")
    if not api_key:
        return None

    if not config.LINKEDIN_ENRICH_ENABLED:
        return None

    try:
        raw = _call_proxycurl(linkedin_url, api_key)
        time.sleep(_REQUEST_DELAY)
    except Exception as exc:
        logger.warning("Proxycurl fetch failed for %s: %s", linkedin_url, exc)
        return None

    if not raw:
        return None

    # Normalise education entries
    education = []
    for edu in (raw.get("education") or []):
        if edu.get("school"):
            education.append({
                "school": edu.get("school", ""),
                "degree": edu.get("degree_name", ""),
                "field_of_study": edu.get("field_of_study", ""),
                "starts_at": (edu.get("starts_at") or {}).get("year"),
                "ends_at": (edu.get("ends_at") or {}).get("year"),
            })

    # Normalise experience entries
    experiences = []
    for exp in (raw.get("experiences") or []):
        if exp.get("company"):
            experiences.append({
                "company": exp.get("company", ""),
                "title": exp.get("title", ""),
                "location": exp.get("location", ""),
                "starts_at": (exp.get("starts_at") or {}).get("year"),
                "ends_at": (exp.get("ends_at") or {}).get("year"),
            })

    return {
        "full_name": raw.get("full_name", ""),
        "headline": raw.get("headline", ""),
        "city": raw.get("city", ""),
        "country_full_name": raw.get("country_full_name", ""),
        "education": education,
        "experiences": experiences,
    }
