"""
tools/outreach.py — Generate a first-touch outreach email for a lead.

This is a DRAFT generator. The analyst must review and personalise before sending.
Never send outreach automatically — always present for approval first.

Usage:
    from tools.outreach import generate_outreach
    from enrichment.engine import CompanyProfile

    draft = generate_outreach(profile)
    print(draft)

CLI:
    python enrich.py "Company Name" --outreach
"""

from __future__ import annotations

import logging

import anthropic

import config
from enrichment.engine import CompanyProfile

logger = logging.getLogger(__name__)

OUTREACH_SYSTEM_PROMPT = """You are a VC analyst at Carica VC, a Central American venture capital fund.
You are drafting a short, warm, first-touch outreach email to a startup founder.

Carica VC invests $100K+ in tech startups with a Central American or Dominican Republic founder connection,
at pre-seed, seed, or Series A stage.

Tone: Direct, genuine, respectful of the founder's time. No buzzwords or corporate filler.
Length: 4–6 sentences maximum. Subject line included.
Language: Match the founder's likely language (Spanish if CA/DR, English otherwise).
             If uncertain, write in English.

Output format (plain text, no markdown):
Subject: [subject line]

[email body]

[Analyst name placeholder: {ANALYST_NAME}]
Carica VC
"""

OUTREACH_USER_TEMPLATE = """Draft a first-touch outreach email for this startup.

Company: {name}
One-liner: {one_liner}
Stage: {stage}
Country: {country}
Sector: {sector}
Founder(s): {founders}
Thesis score: {score}/5 — {rationale}

Key personalisation hooks (use at least one):
- Reference something specific about their product or market
- Mention the CA/DR connection if relevant
- Keep it under 6 sentences
- Do NOT mention specific investment amounts or terms
- Do NOT make promises about timelines or outcomes

Return only the email (subject + body). No preamble, no explanation.
"""


def generate_outreach(profile: CompanyProfile) -> str:
    """
    Generate a first-touch outreach email draft for a CompanyProfile.

    Returns the draft as a plain-text string.
    The analyst MUST review and personalise before sending.
    """
    if not profile.name:
        raise ValueError("Cannot generate outreach for a profile with no company name.")

    founders_str = "; ".join(
        f.name for f in profile.founders if f.name
    ) or "Unknown"

    user_msg = OUTREACH_USER_TEMPLATE.format(
        name=profile.name,
        one_liner=profile.one_liner or "N/A",
        stage=profile.stage or "unknown",
        country=profile.country or "unknown",
        sector=profile.sector or "unknown",
        founders=founders_str,
        score=profile.thesis.score if profile.thesis else "N/A",
        rationale=profile.thesis.rationale if profile.thesis else "",
    )

    client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=config.CLAUDE_MODEL_FAST,   # cheap model — outreach doesn't need Opus
        max_tokens=512,
        system=OUTREACH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    draft = message.content[0].text.strip()

    # Safety header so the analyst never mistakes this for a sent email
    header = (
        "━" * 60 + "\n"
        "⚠️  DRAFT — Review and personalise before sending.\n"
        "   Never send this automatically.\n"
        + "━" * 60 + "\n\n"
    )
    return header + draft
