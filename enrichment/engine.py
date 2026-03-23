"""
enrichment/engine.py — Core enrichment logic for Carica Scout.

Primary functions:
  enrich_with_claude(raw_input)  → structured CompanyProfile
  geo_score(founder)             → GeoResult (score 0-4 + signals)
  thesis_score(profile)          → ThesisResult (score 1-5 + rationale)
  find_contact(company, founder) → ContactResult (email + confidence)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import requests

import config
from config import OVER_STAGE_VALUES
from tools.github import format_github_note, github_stats

logger = logging.getLogger(__name__)


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class Founder:
    name: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    education: list[str] = field(default_factory=list)
    previous_roles: list[str] = field(default_factory=list)
    location: str = ""
    phone_prefix: str = ""
    university: str = ""
    company_country: str = ""          # set from CompanyProfile.country before geo_score()
    geo_score: int = 0
    geo_signals: list[str] = field(default_factory=list)
    linkedin_uncertain: bool = False   # True when LinkedIn data is inferred, not scraped


@dataclass
class ContactResult:
    email: str = ""
    confidence: str = "⚠️ Manual"   # High / Medium / Unverified / N/A / ⚠️ Generic / ⚠️ Manual


@dataclass
class ThesisResult:
    score: int = 0
    stars: str = ""
    rationale: str = ""


@dataclass
class CompanyProfile:
    name: str = ""
    website: str = ""
    one_liner: str = ""
    sector: str = ""
    stage: str = ""
    country: str = ""
    founders: list[Founder] = field(default_factory=list)
    thesis: ThesisResult = field(default_factory=ThesisResult)
    contact: ContactResult = field(default_factory=ContactResult)
    source: str = ""
    date_found: str = ""
    notes: str = ""
    raw_input: str = ""
    portfolio_fit_score: int = 0                        # Phase 2: deterministic pattern match score (0-4)
    portfolio_fit_note: str = ""                        # Phase 2: human-readable explanation of score
    non_ca_founder_building_in_region: bool = False     # Phase 3b: non-regional founder, CA/DR company


# ── Calibration loader ──────────────────────────────────────────────────────

def normalize_for_fp(name: str) -> str:
    """Normalize a company name for false positive matching (lowercase + strip legal suffixes)."""
    n = name.lower().strip()
    for suffix in config.LEGAL_SUFFIXES:
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    return n


def load_calibration(path: str = "CALIBRATION.md") -> dict:
    """
    Parse CALIBRATION.md into a structured dict.
    Warns but does not crash if the file is missing.
    """
    result: dict = {
        "false_positives": [],
        "founding_year_threshold": 2019,
        "raise_threshold_usd": 5_000_000,
        "sector_adjustments": [],
        "query_refinements": {},
        "meta": {},
    }

    cal_path = config.ROOT / path
    if not cal_path.exists():
        logger.warning(
            "⚠️  CALIBRATION.md not found — running without calibration. "
            "Run `python feedback.py` to generate."
        )
        return result

    text = cal_path.read_text(encoding="utf-8")

    def extract_section(section_name: str) -> str:
        pattern = (
            rf"<!--\s*feedback\.py:{re.escape(section_name)}:start\s*-->"
            rf"(.*?)"
            rf"<!--\s*feedback\.py:{re.escape(section_name)}:end\s*-->"
        )
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else ""

    # False positives
    fp_block = extract_section("false_positives")
    fps = []
    for line in fp_block.splitlines():
        line = line.strip()
        if line.startswith("- "):
            fps.append(normalize_for_fp(line[2:].strip()))
    result["false_positives"] = fps

    # Founding year threshold
    year_block = extract_section("founding_year")
    m = re.search(r"\d{4}", year_block)
    if m:
        result["founding_year_threshold"] = int(m.group(0))

    # Raise threshold
    raise_block = extract_section("raise_threshold")
    m = re.search(r"\$?([\d,]+)\s*(M|K)?", raise_block)
    if m:
        val = int(m.group(1).replace(",", ""))
        mult = (m.group(2) or "").upper()
        if mult == "M":
            val *= 1_000_000
        elif mult == "K":
            val *= 1_000
        result["raise_threshold_usd"] = val

    # Sector adjustments — format: "- Sector | geo_filter | delta | condition | reason"
    adj_block = extract_section("sector_adjustments")
    adjustments = []
    for line in adj_block.splitlines():
        line = line.strip()
        if line.startswith("- ") and "|" in line:
            parts = [p.strip() for p in line[2:].split("|")]
            if len(parts) >= 4:
                try:
                    adjustments.append({
                        "sector": parts[0].lower(),
                        "geo_filter": parts[1].lower(),
                        "delta": int(parts[2]),
                        "condition": parts[3].lower(),
                        "reason": parts[4] if len(parts) > 4 else "",
                    })
                except (ValueError, IndexError):
                    pass
    result["sector_adjustments"] = adjustments

    # Query refinements — format: '- tag: add terms "terms" — reason'
    qr_block = extract_section("query_refinements")
    refinements: dict[str, str] = {}
    for line in qr_block.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            tag_part, _, rest = line[2:].partition(":")
            tag = tag_part.strip()
            terms_match = re.search(r'"([^"]+)"', rest)
            if terms_match:
                refinements[tag] = terms_match.group(1)
    result["query_refinements"] = refinements

    # Log summary
    fp_count = len(result["false_positives"])
    adj_count = len(result["sector_adjustments"])
    qr_count = len(result["query_refinements"])
    year = result["founding_year_threshold"]
    logger.info(
        f"Calibration loaded: {fp_count} false positive(s) | "
        f"{adj_count} sector adjustment(s) | "
        f"{qr_count} query refinement(s) | "
        f"founding year ≥ {year}"
    )

    return result


# ── GeoScore ────────────────────────────────────────────────────────────────

def geo_score(founder: Founder) -> Founder:
    """
    Score a founder's CA/DR connection across 4 signals.
    Mutates and returns the founder with geo_score and geo_signals set.

    Signals:
      1. University attended is a CA/DR institution
      2. Phone number carries a CA/DR prefix
      3. LinkedIn location or bio mentions a CA/DR city or country
      4. Company HQ or incorporation country is in the region
    """
    signals: list[str] = []

    # Signal 1: University
    uni = (founder.university or "").upper()
    for ca_uni in config.CA_DR_UNIVERSITIES:
        if ca_uni.upper() in uni:
            signals.append(f"University: {founder.university}")
            break

    # Signal 2: Phone prefix — check all valid CA/DR prefixes (DR has +1809/+1829/+1849)
    raw_prefix = (founder.phone_prefix or "").replace("-", "").replace(" ", "")
    for pfx in config.CA_DR_PHONE_PREFIXES:
        if raw_prefix.startswith(pfx.replace("-", "")):
            signals.append(f"Phone prefix: {founder.phone_prefix}")
            break

    # Signal 3: LinkedIn location mentions CA/DR
    location_lower = (founder.location or "").lower()
    for country in config.CA_DR_COUNTRY_NAMES:
        if country.lower() in location_lower:
            signals.append(f"LinkedIn location: {founder.location}")
            break

    # Signal 4: Company HQ/country in region
    company_country = (founder.company_country or "").lower()
    for country in config.CA_DR_COUNTRY_NAMES:
        if country.lower() in company_country:
            signals.append(f"Company HQ: {founder.company_country}")
            break

    founder.geo_score = len(signals)
    founder.geo_signals = signals
    return founder


# ── ThesisScore ─────────────────────────────────────────────────────────────

def thesis_score(profile: CompanyProfile, calibration: dict | None = None) -> ThesisResult:
    """
    Score a CompanyProfile against the Carica VC investment thesis.
    Returns a ThesisResult with score (1-5), stars label, and written rationale.

    calibration: optional dict from load_calibration(). If provided, sector adjustments
    are applied as deterministic post-processing after the base score is computed.
    """
    has_tech = bool(profile.sector)  # sector populated = tech confirmed by Claude
    has_mvp = profile.stage.lower() not in ("idea", "pre-mvp", "")
    is_over_stage = profile.stage.lower() in OVER_STAGE_VALUES

    # Find best founder geo score
    founder_geo_scores = [f.geo_score for f in profile.founders] if profile.founders else [0]
    best_geo = max(founder_geo_scores) if founder_geo_scores else 0

    # Check for traction signals (keywords in one_liner or notes).
    # NOTE: "raised", "seed", "series", "growth" removed — these match late-stage language
    # (e.g. "raised $187M Series C") and inflate scores for out-of-thesis companies.
    # NOTE: early_stage_traction uses Claude's extracted stage field, which is point-in-time.
    # A company that raised seed in 2022 may now be Series B. The founding_year filter in
    # CALIBRATION.md partially mitigates this, but treat seed-stage traction as a soft signal.
    traction_keywords = [
        "revenue", "mrr", "arr", "users", "customers", "clients",
        "traction", "paying",
    ]
    text_lower = (profile.one_liner + " " + profile.notes).lower()
    early_stage_traction = profile.stage.lower() in ("seed", "series-a") and not is_over_stage
    has_traction = (
        any(kw in text_lower for kw in traction_keywords)
        or early_stage_traction
    )

    if best_geo >= 2 and has_tech and has_mvp and has_traction:
        score, rationale = 5, "CA/DR founder confirmed with tech product, live MVP, and traction signals."
    elif best_geo >= 2 and has_tech and has_mvp:
        score, rationale = 4, "CA/DR founder confirmed with tech product and live MVP."
    elif best_geo >= 2 and has_tech:
        score, rationale = 3, "Likely CA/DR founder (2+ geo signals) with tech foundation; MVP status unclear."
    elif best_geo < 2 and has_tech:
        # Check if explicitly targeting region
        region_target = any(
            c.lower() in (profile.one_liner + " " + profile.notes).lower()
            for c in config.CA_DR_COUNTRY_NAMES
        )
        if region_target:
            score, rationale = 2, "External founder explicitly targeting CA/DR market with tech product."
        else:
            score, rationale = 1, "Weak CA/DR signal — fewer than 2 geo signals; flagged for manual analyst review."
    else:
        score, rationale = 1, "Insufficient signals for thesis match; flagged for manual analyst review."

    # ── Cap score for over-stage companies ───────────────────────────────────
    if is_over_stage:
        score = min(score, 2)
        rationale += " [Stage: Series B+ — outside fund mandate; blocked from pipeline.]"

    # ── Post-processing: apply sector adjustments from calibration ────────────
    if calibration:
        for adj in calibration.get("sector_adjustments", []):
            if _matches_sector_adjustment(profile, adj, best_geo):
                adjusted = max(1, min(5, score + adj["delta"]))
                reason = adj.get("reason", "calibration adjustment")
                rationale += f" [Calibration: {reason}]"
                score = adjusted
                break  # apply at most one adjustment

    stars = config.THESIS_SCORE_LABELS.get(score, "")
    return ThesisResult(score=score, stars=stars, rationale=rationale)


def _matches_sector_adjustment(profile: CompanyProfile, adj: dict, best_geo: int) -> bool:
    """Return True if this calibration sector adjustment applies to the given profile."""
    sector = (profile.sector or "").lower()
    geo_filter = adj.get("geo_filter", "").lower()
    condition = adj.get("condition", "").lower()

    # Sector must be present in profile sector string
    if adj.get("sector", "") not in sector:
        return False

    # "outside-ca-dr" filter: only apply when geo_score < 2
    if geo_filter == "outside-ca-dr" and best_geo >= 2:
        return False

    # Explicit condition check
    if "geo_score < 2" in condition and best_geo >= 2:
        return False

    return True


# ── PortfolioFitScore ────────────────────────────────────────────────────────

def portfolio_fit_score(profile: CompanyProfile) -> tuple[int, str]:
    """
    Deterministic portfolio-fit scoring. No LLM calls.
    Matches against structured profile fields + PORTFOLIO_PATTERNS aggregate.
    Returns (score: int 0-4, note: str).

    Signals are designed to be specific: prefer structured field matches over
    free-text scanning to avoid inflating scores on generic tech terms.

    Signal 1: sector matches a top portfolio sector (structured field)
    Signal 2: business model matches portfolio BM strings against sector field
              — NOT free text; "platform"/"api" would fire on everything
    Signal 3: revenue model keyword in one_liner (specific enough to carry signal)
    Signal 4: problem domain match against sector + one_liner
    """
    from portfolio.patterns import PORTFOLIO_PATTERNS

    score = 0
    signals: list[str] = []
    sector = (profile.sector or "").lower()
    one_liner = (profile.one_liner or "").lower()

    # Signal 1: sector matches a top portfolio sector (structured field match)
    for s in PORTFOLIO_PATTERNS["top_sectors"]:
        if s.lower() in sector:
            score += 1
            signals.append(f"sector:{s}")
            break

    # Signal 2: business model — match actual portfolio BM strings against sector field
    top_bms = [bm.lower() for bm in PORTFOLIO_PATTERNS["top_business_models"]]
    for bm in top_bms:
        if bm in sector:
            score += 1
            signals.append(f"model:{bm}")
            break

    # Signal 3: revenue model keyword — scan one_liner only (specific enough)
    rev_keywords = {"subscription", "transaction fee", "commission", "net interest", "recurring revenue"}
    for kw in rev_keywords:
        if kw in one_liner:
            score += 1
            signals.append(f"revenue:{kw}")
            break

    # Signal 4: problem domain — match structured sector field + one_liner
    for domain in PORTFOLIO_PATTERNS["top_domains"]:
        if domain.lower() in sector or domain.lower() in one_liner:
            score += 1
            signals.append(f"domain:{domain}")
            break

    note = (
        f"Portfolio fit {score}/4"
        + (f" — {', '.join(signals)}" if signals else " — no pattern match")
    )
    return score, note


# ── FindContact ─────────────────────────────────────────────────────────────

def find_contact(company_website: str, founder: Founder | None = None) -> ContactResult:
    """
    Attempt to find a contact email for a company/founder.

    Priority:
      1. Scrape company website for personal email
      2. Construct pattern + verify via Hunter.io (if key available)
      3. Return constructed pattern as Unverified
      4. Fall back to ⚠️ Manual
    """
    # Step 1: Scrape website for personal email
    if company_website:
        scraped = _scrape_email(company_website)
        if scraped:
            if _is_generic_email(scraped):
                return ContactResult(email=scraped, confidence="⚠️ Generic")
            return ContactResult(email=scraped, confidence="High")

    # Step 2: Try Hunter.io
    hunter_key = config.get_optional_key("HUNTER_API_KEY")
    domain = _extract_domain(company_website)

    if hunter_key and domain:
        hunter_result = _query_hunter(domain, founder, hunter_key)
        if hunter_result:
            return hunter_result

    # Step 3: Construct pattern (unverified)
    if founder and founder.name and domain:
        pattern = _construct_email_pattern(founder.name, domain)
        if pattern:
            confidence = "Unverified"
            return ContactResult(email=pattern, confidence=confidence)

    # Step 4: LinkedIn DM only
    if founder and founder.linkedin_url:
        return ContactResult(email="", confidence="N/A")

    return ContactResult(email="", confidence="⚠️ Manual")


def _scrape_email(url: str) -> str:
    """Scrape a URL for email addresses."""
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": config.USER_AGENT},
        )
        emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", resp.text)
        # Filter out common false positives
        emails = [e for e in emails if not e.endswith((".png", ".jpg", ".svg"))]
        if emails:
            return emails[0]
    except Exception:
        pass
    return ""


def _is_generic_email(email: str) -> bool:
    generic_prefixes = ("info", "hello", "contact", "hola", "support", "team", "hi")
    local = email.split("@")[0].lower()
    return local in generic_prefixes


def _extract_domain(url: str) -> str:
    match = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return match.group(1) if match else ""


def _query_hunter(domain: str, founder: Founder | None, api_key: str) -> ContactResult | None:
    try:
        params: dict[str, Any] = {"domain": domain, "api_key": api_key}
        if founder and founder.name:
            parts = founder.name.strip().split()
            if len(parts) >= 2:
                params["first_name"] = parts[0]
                params["last_name"] = parts[-1]
        resp = requests.get(
            f"{config.HUNTER_BASE_URL}/email-finder",
            params=params,
            timeout=config.REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            logger.warning("Hunter.io auth failed — check HUNTER_API_KEY.")
            return None
        if resp.status_code == 429:
            logger.warning("Hunter.io rate limit reached — skipping for this run.")
            return None
        resp.raise_for_status()
        data = resp.json().get("data", {})
        email = data.get("email", "")
        if email:
            return ContactResult(email=email, confidence="Medium")
    except Exception as exc:
        logger.warning(f"Hunter.io lookup failed for {domain}: {exc}")
    return None


def _construct_email_pattern(name: str, domain: str) -> str:
    parts = name.strip().lower().split()
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        return f"{first}.{last}@{domain}"
    elif parts:
        return f"{parts[0]}@{domain}"
    return ""


# ── EnrichWithClaude ────────────────────────────────────────────────────────

ENRICH_SYSTEM_PROMPT = """You are the Carica Scout enrichment engine.
Given raw information about a startup (name, URL, text, or a mix), extract a structured profile.

Return ONLY valid JSON matching this schema:
{
  "name": "Company name",
  "website": "https://...",
  "one_liner": "One sentence description",
  "sector": "e.g. Fintech / Healthtech / SaaS / ...",
  "stage": "pre-seed / seed / series-a / unknown",
  "country": "HQ country",
  "founders": [
    {
      "name": "Full Name",
      "linkedin_url": "https://linkedin.com/in/...",
      "github_url": "https://github.com/handle or empty string",
      "education": ["University Name (Year)"],
      "previous_roles": ["Role at Company"],
      "location": "City, Country (from LinkedIn)",
      "phone_prefix": "+506",
      "university": "University short name"
    }
  ],
  "notes": "Anything unusual or noteworthy"
}

Rules:
- Use null for unknown fields, not empty strings
- Never invent information not present in the source
- If multiple founders exist, include all
- Sector must reflect the tech foundation (software, platform, API, data, hardware)
- Stage: only use known stages — if unclear, use "unknown"
"""


def enrich_with_claude(
    raw_input: str,
    source: str = "manual",
    calibration: dict | None = None,
) -> CompanyProfile:
    """
    Use Claude to extract a structured CompanyProfile from raw input.
    raw_input can be: company name, URL, pasted text, email, WhatsApp message, etc.

    calibration: optional dict from load_calibration(). If provided, calibration context
    is injected into the Claude extraction prompt and sector adjustments are applied post-scoring.

    If TAVILY_API_KEY is set (or a website can be inferred), web research is
    fetched first and appended to raw_input to give Claude richer context.
    """
    from tools.research import research_company

    # ── Step 0: Quick heuristic to detect URL or bare name in raw_input ──────
    url_match = re.search(r"https?://\S+", raw_input)
    detected_url = url_match.group(0) if url_match else None
    # For bare names, research_company will do a Tavily search by name alone
    detected_name = raw_input.strip().split("\n")[0][:80]

    research_context = research_company(detected_name, detected_url)

    # Append web research so Claude has real data, not just the raw input
    enriched_input = raw_input
    if research_context:
        enriched_input = (
            f"{raw_input}\n\n"
            f"--- Additional web research (use to fill gaps, do not invent) ---\n"
            f"{research_context}"
        )

    # ── Build system prompt with calibration context ──────────────────────────
    system_prompt = ENRICH_SYSTEM_PROMPT
    if calibration:
        fp_list = calibration.get("false_positives", [])
        year = calibration.get("founding_year_threshold", 2019)
        amount = calibration.get("raise_threshold_usd", 5_000_000)
        amount_str = f"${amount // 1_000_000}M" if amount >= 1_000_000 else f"${amount:,}"
        fp_display = ", ".join(fp_list[:20]) if fp_list else "none"

        cal_context = (
            f"\n\n## Calibration Context (learned from past analyst decisions)\n"
            f"Known false positives — add to notes field if matched: {fp_display}\n"
            f"Caution: flag companies founded before {year} in the notes field.\n"
            f"Caution: flag leads with total known funding > {amount_str} in the notes field.\n\n"
            f"Extraction priorities:\n"
            f"- Check all 4 geo signals for every founder explicitly\n"
            f"- Do not infer early-stage from news about large funding rounds\n"
            f"- Always note founding year and total known funding in notes if found\n"
        )
        system_prompt = ENRICH_SYSTEM_PROMPT + cal_context

    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))

    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": enriched_input}],
    )

    raw_json = message.content[0].text.strip()

    # Strip markdown code fences if present
    raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
    raw_json = re.sub(r"\s*```$", "", raw_json)

    # If Claude added a preamble before the JSON object, extract just the JSON
    brace_start = raw_json.find("{")
    brace_end = raw_json.rfind("}")
    if brace_start > 0 and brace_end > brace_start:
        raw_json = raw_json[brace_start:brace_end + 1]

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON: {exc}\n\nRaw:\n{raw_json}") from exc

    # Build profile
    profile = CompanyProfile(
        name=data.get("name") or "",
        website=data.get("website") or "",
        one_liner=data.get("one_liner") or "",
        sector=data.get("sector") or "",
        stage=(data.get("stage") or "").lower(),
        country=data.get("country") or "",
        notes=data.get("notes") or "",
        source=source,
        raw_input=raw_input,
    )

    # Build founders
    for fd in data.get("founders") or []:
        founder = Founder(
            name=fd.get("name") or "",
            linkedin_url=fd.get("linkedin_url") or "",
            github_url=fd.get("github_url") or "",
            education=fd.get("education") or [],
            previous_roles=fd.get("previous_roles") or [],
            location=fd.get("location") or "",
            phone_prefix=fd.get("phone_prefix") or "",
            university=fd.get("university") or "",
        )
        # Attach company country for geo signal 4
        founder.company_country = profile.country
        geo_score(founder)
        profile.founders.append(founder)

    # Flag founders whose LinkedIn data is inferred, not scraped
    uncertain_founders = []
    for f in profile.founders:
        if f.linkedin_url and not f.location and f.geo_score <= 1:
            f.linkedin_uncertain = True
            uncertain_founders.append(f.name)
    if uncertain_founders:
        flag = f"⚠️ LinkedIn data unverified for: {', '.join(uncertain_founders)} — geo signals based on inferred data only."
        profile.notes = (profile.notes + "\n" + flag).strip() if profile.notes else flag

    # Fetch GitHub stats for founders with a known GitHub URL
    for f in profile.founders:
        if f.github_url:
            try:
                stats = github_stats(f.github_url)
                if stats:
                    note_line = f"GitHub ({f.name}): {format_github_note(stats)}"
                    profile.notes = (profile.notes + "\n" + note_line).strip()
            except Exception as exc:
                logger.warning("GitHub lookup failed for %s: %s", f.name, exc)

    # Score thesis (pass calibration so sector adjustments are applied)
    profile.thesis = thesis_score(profile, calibration=calibration)

    # ── Portfolio-fit scoring (deterministic, no LLM) ─────────────────────
    fit_score, fit_note = portfolio_fit_score(profile)
    profile.portfolio_fit_score = fit_score
    profile.portfolio_fit_note = fit_note

    # ── Non-CA founder building in region (annotation flag) ───────────────
    # A CA HQ already fires Signal 4 in geo_score(), so geo_score ≥ 1 is common
    # even for non-regional founders. This flag targets the case where the
    # founder has < 2 geo signals (not clearly CA/DR) BUT the company is
    # explicitly operating in the region. It is analyst-visibility only —
    # not a pipeline gate.
    _best_geo = max((f.geo_score for f in profile.founders), default=0)
    # Use full country names only — 2-letter codes (NI, PA, DO…) are too short
    # and cause false positives (e.g. "NI" matches "united", "DO" matches "done").
    # This mirrors the same conservative approach used in geo_prescreen().
    _region_full_names = {c.lower() for c in config.TARGET_COUNTRIES.keys()}
    _text_lower = (
        (profile.one_liner or "")
        + " " + (profile.notes or "")
        + " " + (profile.country or "")
    ).lower()
    if _best_geo < 2 and any(c in _text_lower for c in _region_full_names):
        profile.non_ca_founder_building_in_region = True

    # Find contact (use first founder if available)
    primary_founder = profile.founders[0] if profile.founders else None
    profile.contact = find_contact(profile.website, primary_founder)

    return profile
