"""
tools/briefing.py — Generate a pre-meeting analyst brief for a lead.

Produces a 1-page structured brief the analyst can read in 2 minutes
before a founder call. Covers: company snapshot, founder background,
thesis fit, key questions to ask, and red flags to probe.

Usage:
    from tools.briefing import generate_briefing
    from enrichment.engine import CompanyProfile

    brief = generate_briefing(profile)
    print(brief)

CLI:
    python enrich.py "Company Name" --brief
"""

from __future__ import annotations

import logging

import anthropic

import config
from enrichment.engine import CompanyProfile

logger = logging.getLogger(__name__)

BRIEFING_SYSTEM_PROMPT = """You are a senior VC analyst preparing a pre-meeting brief
for a partner at Carica VC, a Central American venture capital fund that invests
$100K+ in tech startups at pre-seed, seed, or Series A stage.

The brief must be scannable in under 2 minutes. Use short bullet points.
Be honest about gaps and uncertainties — never pad with filler.

Output structure (plain text, use ── section headers):

── Snapshot ──
• Company: [name] | [stage] | [country]
• Sector: [sector]
• Product: [one-liner]
• Website: [url]

── Founders ──
• [Name] — [geo score/4 signals] — [education] — [key prior role]
• (repeat per founder)
• ⚠️ LinkedIn data unverified: [list if applicable]

── Thesis Fit ──
• Score: [X/5] — [rationale]
• CA/DR signals: [list signals or "None confirmed"]
• Tech foundation: [yes/no + brief reason]
• MVP/Traction: [what's known]

── Contact ──
• Best email: [email] ([confidence])
• LinkedIn DM fallback: [url or N/A]

── Key Questions to Ask ──
1. [Question targeting biggest unknown]
2. [Question about business model / revenue]
3. [Question about CA/DR market strategy or founder connection]
4. [Question about use of funds / runway]

── Red Flags to Probe ──
• [Flag 1 — be specific, not generic]
• [Flag 2]
• (max 3 flags)

── Notes ──
[Anything unusual from the enrichment run, or "None"]
"""

BRIEFING_USER_TEMPLATE = """Generate a pre-meeting brief for this company.

Company: {name}
Website: {website}
One-liner: {one_liner}
Sector: {sector}
Stage: {stage}
Country: {country}

Founders:
{founders_detail}

Thesis score: {score}/5
Thesis rationale: {rationale}

Contact: {contact_email} [{contact_confidence}]

Source: {source}
Date found: {date_found}

Notes from enrichment: {notes}
"""


def _format_founders(profile: CompanyProfile) -> str:
    if not profile.founders:
        return "  • Unknown"
    lines = []
    for f in profile.founders:
        signals = ", ".join(f.geo_signals) if f.geo_signals else "no geo signals"
        edu = "; ".join(f.education[:2]) if f.education else "unknown"
        roles = "; ".join(f.previous_roles[:2]) if f.previous_roles else "unknown"
        uncertain = " ⚠️ LinkedIn unverified" if f.linkedin_uncertain else ""
        lines.append(
            f"  • {f.name or 'Unknown'} — geo {f.geo_score}/4 ({signals}){uncertain}\n"
            f"    Education: {edu}\n"
            f"    Prior roles: {roles}\n"
            f"    LinkedIn: {f.linkedin_url or 'N/A'}"
        )
    return "\n".join(lines)


def generate_briefing(profile: CompanyProfile) -> str:
    """
    Generate a pre-meeting analyst brief for a CompanyProfile.

    Returns a structured plain-text brief.
    """
    if not profile.name:
        raise ValueError("Cannot generate a brief for a profile with no company name.")

    user_msg = BRIEFING_USER_TEMPLATE.format(
        name=profile.name,
        website=profile.website or "N/A",
        one_liner=profile.one_liner or "N/A",
        sector=profile.sector or "unknown",
        stage=profile.stage or "unknown",
        country=profile.country or "unknown",
        founders_detail=_format_founders(profile),
        score=profile.thesis.score if profile.thesis else "N/A",
        rationale=profile.thesis.rationale if profile.thesis else "N/A",
        contact_email=profile.contact.email if profile.contact else "N/A",
        contact_confidence=profile.contact.confidence if profile.contact else "N/A",
        source=profile.source or "manual",
        date_found=profile.date_found or "today",
        notes=profile.notes or "None",
    )

    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=config.CLAUDE_MODEL,   # full model — brief quality matters
        max_tokens=1024,
        system=BRIEFING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    brief = message.content[0].text.strip()

    header = (
        "━" * 60 + "\n"
        f"  PRE-MEETING BRIEF — {profile.name}\n"
        + "━" * 60 + "\n\n"
    )
    return header + brief
