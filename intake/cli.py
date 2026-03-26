"""
intake/cli.py — Local CLI wrapper for the inbound intake handler.

Usage:
    python -m intake.cli "Company Name"
    python -m intake.cli "Company Name" --referrer "LP Name"
    python -m intake.cli "Company Name" --referrer "LP Name" --notes "met at INCAE"

Exits with code 0 on success (created/duplicate/portfolio/skipped),
code 1 on pipeline error.
"""

from __future__ import annotations

import argparse
import sys

from intake.handler import handle_intake


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Carica Scout — inbound lead intake",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m intake.cli 'Paggo'\n"
            "  python -m intake.cli 'Paggo' --referrer 'LP Name' --notes 'met at INCAE'\n"
        ),
    )
    parser.add_argument("company", help="Company name or brief description")
    parser.add_argument("--referrer", default="unknown", help="Who referred this lead (default: unknown)")
    parser.add_argument("--notes", default="", help="Optional context notes")

    args = parser.parse_args()
    result = handle_intake(args.company, referrer=args.referrer, notes=args.notes)

    status = result["status"]
    company = result["company"]
    score = result["thesis_score"]
    skip_reason = result["skip_reason"]

    if status == "created":
        score_str = f" (thesis: {score}/5)" if score is not None else ""
        print(f"✅ {company} pushed to Notion{score_str}")
        return 0
    elif status == "duplicate":
        print(f"⏭  {company} already in Notion — skipped")
        return 0
    elif status == "portfolio":
        print(f"⏭  {company} is a portfolio company — skipped")
        return 0
    elif status == "skipped":
        print(f"⏭  {company} filtered by pre-screen: {skip_reason}")
        return 0
    else:  # error
        print(f"❌ Intake failed for '{company}': {skip_reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
