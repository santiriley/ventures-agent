"""
intake/handler.py — Inbound lead intake handler for Carica Scout.

Accepts a company name plus optional referrer and notes, runs the two-stage
enrichment pipeline (light_enrich gate → full enrichment → Notion push),
and returns a structured result dict.

Compatible with:
  - Local CLI:  python -m intake.cli "Company Name" --referrer "LP Name"
  - Google Cloud Function: wrap handle_intake() in a Flask/functions-framework handler

Usage:
    from intake.handler import handle_intake

    result = handle_intake("Paggo", referrer="LP Name", notes="met at INCAE event")
    # → {"status": "created", "company": "Paggo", "thesis_score": 4, "skip_reason": None}
"""

from __future__ import annotations

import logging

from enrichment.engine import light_enrich, light_thesis_check, enrich_with_claude
from notion.writer import push_lead

logger = logging.getLogger(__name__)


def handle_intake(
    company: str,
    referrer: str = "unknown",
    notes: str = "",
) -> dict:
    """
    Run the full intake pipeline for a single inbound company name.

    Steps:
      1. Validate — company must be a non-empty string
      2. Light enrich — cheap pre-screen (Haiku + 1 basic Tavily)
      3. If filtered by light_thesis_check → return "skipped" immediately
      4. Full enrichment — enrich_with_claude() with source tag "inbound:{referrer}"
      5. Push to Notion — push_lead()

    Returns:
      {
        "status": "created" | "duplicate" | "portfolio" | "skipped" | "error",
        "company": str,
        "thesis_score": int | None,
        "skip_reason": str | None,
      }

    Never raises — all errors are caught and returned as status="error".
    """
    company = (company or "").strip()
    if not company:
        return {
            "status": "error",
            "company": "",
            "thesis_score": None,
            "skip_reason": "company name is required",
        }

    try:
        # Step 2: Light enrich pre-screen
        light = light_enrich(company)
        if not light_thesis_check(light):
            skip_reason = light.get("skip_reason") or "below thesis threshold"
            logger.info(f"[INTAKE SKIPPED] {company} — {skip_reason}")
            return {
                "status": "skipped",
                "company": company,
                "thesis_score": None,
                "skip_reason": skip_reason,
            }

        # Step 3: Full enrichment
        source = f"inbound:{referrer}"
        raw_input = company
        if notes:
            raw_input = f"{company}\n\nContext: {notes}"

        profile = enrich_with_claude(raw_input, source=source)

        if notes and not profile.notes:
            profile.notes = notes
        elif notes:
            profile.notes = notes + "\n" + profile.notes

        # Step 4: Push to Notion
        push_result = push_lead(profile)

        thesis_score = profile.thesis.score if profile.thesis else None
        logger.info(f"[INTAKE {push_result.upper()}] {company} (referrer: {referrer})")

        return {
            "status": push_result,
            "company": profile.name or company,
            "thesis_score": thesis_score,
            "skip_reason": None,
        }

    except Exception as exc:
        logger.exception(f"Intake pipeline failed for '{company}': {exc}")
        return {
            "status": "error",
            "company": company,
            "thesis_score": None,
            "skip_reason": str(exc),
        }
