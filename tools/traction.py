"""
tools/traction.py — Deterministic traction verification for Carica Scout.

Checks public signals (GitHub, App Store, Play Store) without LLM calls.
Each source fails open (returns None on error) so missing data never blocks the pipeline.

No paid API keys required for the MVP:
  - GitHub API: free, 60 req/hour unauthenticated
  - iTunes Search API: free, no key
  - Play Store: uses google-play-scraper package (optional; gracefully skipped if not installed)

Usage:
    from tools.traction import verify_traction, TractionSnapshot
    from enrichment.engine import CompanyProfile

    snapshot = verify_traction(profile)
    print(snapshot.verified_signals)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

import config

logger = logging.getLogger(__name__)


@dataclass
class TractionSnapshot:
    github_stars: int | None = None
    github_last_commit_days: int | None = None
    app_store_rating: float | None = None
    app_store_reviews: int | None = None
    play_store_rating: float | None = None
    play_store_reviews: int | None = None
    verified_signals: list[str] = field(default_factory=list)


def _check_github(profile: "CompanyProfile") -> dict:  # type: ignore[name-defined]
    """
    Check GitHub for founders with known github_url, plus a company name search.
    Returns dict with stars and last_commit_days, or empty dict on failure.
    """
    from tools.github import github_stats

    for founder in (profile.founders or []):
        if founder.github_url:
            try:
                stats = github_stats(founder.github_url)
                if stats:
                    # github_stats returns: {username, public_repos, followers, top_languages, last_active, profile_url}
                    # last_active is a string like "3 days ago" or an ISO date — parse it
                    last_active = stats.get("last_active") or ""
                    days = _parse_days_ago(last_active)
                    # Use total public_repos as a proxy for stars (github_stats doesn't return stars directly)
                    return {
                        "stars": stats.get("followers", 0),  # followers as engagement proxy
                        "last_commit_days": days,
                        "repos": stats.get("public_repos", 0),
                    }
            except Exception as exc:
                logger.debug("GitHub traction check failed for %s: %s", founder.name, exc)

    return {}


def _parse_days_ago(last_active: str) -> int | None:
    """Parse 'N days ago' or ISO date into integer days. Returns None if unparseable."""
    if not last_active:
        return None
    try:
        import re
        import datetime
        m = re.search(r"(\d+)\s+day", last_active)
        if m:
            return int(m.group(1))
        # Try ISO date
        m2 = re.match(r"(\d{4}-\d{2}-\d{2})", last_active)
        if m2:
            dt = datetime.date.fromisoformat(m2.group(1))
            return (datetime.date.today() - dt).days
    except Exception:
        pass
    return None


def _check_app_store(company_name: str) -> dict:
    """
    Search iTunes Search API (free, no key) for the company's iOS app.
    Returns dict with rating and review_count, or empty dict on failure.
    """
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={
                "term": company_name,
                "entity": "software",
                "country": "us",
                "limit": 3,
            },
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return {}

        # Find the best match (first result with a rating)
        for r in results:
            rating = r.get("averageUserRating")
            reviews = r.get("userRatingCount", 0)
            track_name = (r.get("trackName") or "").lower()
            company_lower = company_name.lower()
            # Only use results where the app name reasonably matches the company
            if rating and (company_lower in track_name or track_name in company_lower):
                return {"rating": round(float(rating), 1), "reviews": int(reviews)}

        # Fall back to first rated result if no name match
        for r in results:
            if r.get("averageUserRating"):
                return {
                    "rating": round(float(r["averageUserRating"]), 1),
                    "reviews": int(r.get("userRatingCount", 0)),
                }
    except Exception as exc:
        logger.debug("App Store lookup failed for %s: %s", company_name, exc)

    return {}


def _check_play_store(company_name: str) -> dict:
    """
    Check Google Play Store for the company's Android app.
    Uses google-play-scraper package (optional; gracefully skipped if not installed).
    Returns dict with rating and review_count, or empty dict on failure.
    """
    try:
        from google_play_scraper import search  # type: ignore
        results = search(company_name, n_hits=3, lang="en", country="us")
        if not results:
            return {}

        company_lower = company_name.lower()
        for r in results:
            title = (r.get("title") or "").lower()
            score = r.get("score")
            ratings = r.get("ratings", 0)
            if score and (company_lower in title or title in company_lower):
                return {"rating": round(float(score), 1), "reviews": int(ratings)}

        # Fall back to first scored result
        for r in results:
            if r.get("score"):
                return {
                    "rating": round(float(r["score"]), 1),
                    "reviews": int(r.get("ratings", 0)),
                }
    except ImportError:
        logger.debug("google-play-scraper not installed — skipping Play Store check.")
    except Exception as exc:
        logger.debug("Play Store lookup failed for %s: %s", company_name, exc)

    return {}


def verify_traction(profile: "CompanyProfile") -> TractionSnapshot:  # type: ignore[name-defined]
    """
    Run deterministic traction checks for a company. No LLM calls.

    Sources checked (each fails open independently):
    1. GitHub via tools/github.py — founder profile metrics
    2. App Store via iTunes Search API (free, no key)
    3. Play Store via google-play-scraper (optional package)

    Returns a TractionSnapshot with populated fields and human-readable
    verified_signals strings (e.g. "iOS App Store: 4.6 ⭐ (89 reviews)").
    """
    if not config.TRACTION_VERIFY_ENABLED:
        return TractionSnapshot()

    snapshot = TractionSnapshot()
    company_name = profile.name or ""

    if not company_name:
        return snapshot

    # 1. GitHub
    gh = _check_github(profile)
    if gh:
        snapshot.github_stars = gh.get("stars")
        snapshot.github_last_commit_days = gh.get("last_commit_days")
        parts = []
        if snapshot.github_stars is not None:
            parts.append(f"{snapshot.github_stars} followers")
        if snapshot.github_last_commit_days is not None:
            parts.append(f"last commit {snapshot.github_last_commit_days}d ago")
        elif gh.get("repos"):
            parts.append(f"{gh['repos']} public repos")
        if parts:
            snapshot.verified_signals.append(f"GitHub: {', '.join(parts)}")

    # 2. App Store
    app = _check_app_store(company_name)
    if app:
        snapshot.app_store_rating = app.get("rating")
        snapshot.app_store_reviews = app.get("reviews")
        if snapshot.app_store_rating is not None:
            r_str = f"{snapshot.app_store_rating} ⭐"
            if snapshot.app_store_reviews:
                r_str += f" ({snapshot.app_store_reviews:,} reviews)"
            snapshot.verified_signals.append(f"iOS App Store: {r_str}")

    # 3. Play Store
    play = _check_play_store(company_name)
    if play:
        snapshot.play_store_rating = play.get("rating")
        snapshot.play_store_reviews = play.get("reviews")
        if snapshot.play_store_rating is not None:
            r_str = f"{snapshot.play_store_rating} ⭐"
            if snapshot.play_store_reviews:
                r_str += f" ({snapshot.play_store_reviews:,} ratings)"
            snapshot.verified_signals.append(f"Play Store: {r_str}")

    return snapshot
