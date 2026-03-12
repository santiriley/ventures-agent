"""
tools/github.py — GitHub public API lookup for Carica Scout.

No auth token required for basic public reads (60 req/hour unauthenticated).
Used to validate the "technology foundation" thesis criterion by checking
whether a founder or company has active public GitHub activity.

Usage:
    from tools.github import github_stats

    stats = github_stats("octocat")
    # Returns dict with repo count, stars, top languages, last active date
    # Returns None if user not found or request fails
"""

from __future__ import annotations

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


def github_stats(username_or_url: str) -> dict | None:
    """
    Fetch public GitHub stats for a user or org.

    Accepts either a bare username ("octocat") or a full GitHub URL
    ("https://github.com/octocat" or "https://github.com/octocat/repo").

    Returns a dict with:
        username        str
        public_repos    int
        followers       int
        top_languages   list[str]   (up to 3, by repo count)
        last_active     str         (ISO date of most recently pushed repo)
        profile_url     str

    Returns None if the user is not found or the request fails.
    """
    username = _extract_username(username_or_url)
    if not username:
        return None

    user_data = _get_user(username)
    if not user_data:
        return None

    repos = _get_repos(username)
    top_languages = _top_languages(repos)
    last_active = _last_pushed(repos)

    return {
        "username": username,
        "public_repos": user_data.get("public_repos", 0),
        "followers": user_data.get("followers", 0),
        "top_languages": top_languages,
        "last_active": last_active,
        "profile_url": f"https://github.com/{username}",
    }


def format_github_note(stats: dict) -> str:
    """Format GitHub stats as a one-line note for the company profile."""
    langs = ", ".join(stats["top_languages"]) if stats["top_languages"] else "—"
    return (
        f"GitHub: {stats['profile_url']} · "
        f"{stats['public_repos']} public repos · "
        f"Languages: {langs} · "
        f"Last active: {stats['last_active'] or 'unknown'}"
    )


# ── Internals ─────────────────────────────────────────────────────────────────

def _extract_username(value: str) -> str:
    """Extract a GitHub username from a URL or bare string."""
    value = value.strip().rstrip("/")
    if "github.com/" in value:
        # https://github.com/username  or  https://github.com/username/repo
        parts = value.split("github.com/", 1)[-1].split("/")
        return parts[0] if parts else ""
    # Bare username
    return value if value and "/" not in value else ""


def _get_user(username: str) -> dict | None:
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/users/{username}",
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/vnd.github+json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        time.sleep(config.REQUEST_DELAY)
        if resp.status_code == 404:
            logger.debug(f"GitHub user not found: {username}")
            return None
        if resp.status_code == 403:
            logger.warning("GitHub rate limit hit — skipping GitHub lookup.")
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning(f"GitHub user lookup failed for {username}: {exc}")
        return None


def _get_repos(username: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{GITHUB_API_BASE}/users/{username}/repos",
            params={"per_page": 30, "sort": "pushed"},
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/vnd.github+json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        time.sleep(config.REQUEST_DELAY)
        if resp.status_code != 200:
            return []
        return resp.json()
    except Exception as exc:
        logger.warning(f"GitHub repos lookup failed for {username}: {exc}")
        return []


def _top_languages(repos: list[dict]) -> list[str]:
    """Return top 3 languages by frequency across repos."""
    lang_counts: dict[str, int] = {}
    for repo in repos:
        lang = repo.get("language")
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    sorted_langs = sorted(lang_counts, key=lambda l: lang_counts[l], reverse=True)
    return sorted_langs[:3]


def _last_pushed(repos: list[dict]) -> str:
    """Return the most recent push date across repos (YYYY-MM-DD)."""
    dates = [r.get("pushed_at", "") for r in repos if r.get("pushed_at")]
    if not dates:
        return ""
    latest = max(dates)
    return latest[:10]  # trim to YYYY-MM-DD
