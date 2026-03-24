"""
enrich.py — On-demand CLI enrichment tool for Carica Scout.

Usage:
  python enrich.py "Company Name"           # Enrich by name → Notion
  python enrich.py "https://company.com"    # Enrich by URL → Notion
  python enrich.py --inbound                # Paste any text until "done"
  python enrich.py --batch leads.txt        # File with one name/URL per line
  python enrich.py "Name" --no-push         # Enrich without pushing to Notion
"""

from __future__ import annotations

import argparse
import csv
import datetime
import logging
import sys
from pathlib import Path

import config
from enrichment.engine import enrich_with_claude, load_calibration, normalize_for_fp, CompanyProfile
from notion.writer import push_lead

# ── Load calibration at startup ───────────────────────────────────────────────
_CALIBRATION: dict = {}

def _init_calibration() -> None:
    global _CALIBRATION
    _CALIBRATION = load_calibration()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Display ──────────────────────────────────────────────────────────────────

def print_profile(profile: CompanyProfile) -> None:
    """Print a formatted terminal summary of the enriched profile."""
    thesis_stars = profile.thesis.stars.split("—")[0].strip() if profile.thesis else ""
    print()
    print("─" * 60)
    print(f"  {profile.name or 'Unknown'}")
    print("─" * 60)
    print(f"  Website:   {profile.website or 'N/A'}")
    print(f"  One-liner: {profile.one_liner or 'N/A'}")
    print(f"  Sector:    {profile.sector or 'N/A'}")
    print(f"  Stage:     {profile.stage or 'N/A'}")
    print(f"  Country:   {profile.country or 'N/A'}")
    print()
    if profile.founders:
        print("  Founders:")
        for f in profile.founders:
            signals = ", ".join(f.geo_signals) if f.geo_signals else "no signals"
            print(f"    • {f.name or 'Unknown'}")
            print(f"      Geo score: {f.geo_score}/4  ({signals})")
            if f.education:
                print(f"      Education: {'; '.join(f.education)}")
            if f.previous_roles:
                print(f"      Roles:     {'; '.join(f.previous_roles)}")
            if f.linkedin_url:
                print(f"      LinkedIn:  {f.linkedin_url}")
        print()
    if profile.thesis:
        print(f"  Thesis:    {thesis_stars}  ({profile.thesis.score}/5)")
        print(f"             {profile.thesis.rationale}")
    print()
    if profile.contact:
        print(f"  Contact:   {profile.contact.email or '—'}  [{profile.contact.confidence}]")
    if profile.portfolio_fit_note:
        print(f"  Fit:       {profile.portfolio_fit_note}")
    if profile.traction_signals:
        print(f"  Traction:  {' · '.join(profile.traction_signals)}")
    if profile.founder_relevance_note:
        print(f"  Founders:  {profile.founder_relevance_note}")
    if profile.non_ca_founder_building_in_region:
        print("  Flag:      Non-CA founder building in region — manual review recommended")
    if profile.notes:
        print(f"  Notes:     {profile.notes}")
    print(f"  Source:    {profile.source}  |  {profile.date_found or datetime.date.today()}")
    print("─" * 60)
    print()


# ── Single enrich ─────────────────────────────────────────────────────────────

def run_single(
    raw_input: str, no_push: bool = False, source: str = "manual"
) -> tuple[CompanyProfile, str]:
    """
    Enrich a single input, print results, optionally push to Notion.
    Returns (profile, push_result) where push_result is one of:
      "created" | "duplicate" | "portfolio" | "skipped" | "skipped_false_positive"
    """
    # False positive check (no API spend)
    fp_key = normalize_for_fp(raw_input.strip().split("\n")[0][:80])
    if fp_key in _CALIBRATION.get("false_positives", []):
        print(f"\n⏭  Skipped: known false positive ({raw_input[:60].strip()})")
        return CompanyProfile(name=raw_input[:60].strip(), source=source), "skipped_false_positive"

    print(f"\n🔍  Enriching: {raw_input[:80]}...")

    profile = enrich_with_claude(raw_input, source=source, calibration=_CALIBRATION)
    profile.date_found = datetime.date.today().isoformat()

    print_profile(profile)

    if no_push:
        print("  [--no-push] Skipping Notion push.\n")
        return profile, "skipped"

    result = push_lead(profile)
    if result == "created":
        print(f"  ✅  Pushed to Notion: {profile.name}\n")
    elif result == "duplicate":
        print(f"  ⏭️   Duplicate — already in Notion: {profile.name}\n")
    elif result == "portfolio":
        print(f"  🚫  Portfolio company — skipped: {profile.name}\n")

    return profile, result


# ── Inbound mode ──────────────────────────────────────────────────────────────

def run_inbound(no_push: bool = False) -> None:
    """Accept multi-line paste from stdin until user types 'done'."""
    print("\n📥  Inbound triage mode — paste any text (email, WhatsApp, bio, etc.)")
    print("    Type 'done' on a new line when finished.\n")

    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().lower() == "done":
            break
        lines.append(line)

    raw = "\n".join(lines).strip()
    if not raw:
        print("  No input received — exiting.")
        return

    run_single(raw, no_push=no_push, source="inbound")  # result printed inside


# ── Batch mode ────────────────────────────────────────────────────────────────

def run_batch(filepath: str, no_push: bool = False) -> None:
    """Read a file with one name/URL per line and enrich each."""
    path = Path(filepath)
    if not path.exists():
        print(f"  ❌  File not found: {filepath}")
        sys.exit(1)

    lines = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not lines:
        print("  No leads found in file — exiting.")
        return

    total = len(lines)
    results = {"created": 0, "duplicate": 0, "portfolio": 0, "failed": 0}
    # "created" counts new Notion pages (or all processed when --no-push)
    csv_rows: list[dict] = []

    print(f"\n📋  Batch enrichment: {total} leads from {filepath}\n")

    failed_log = config.TMP_DIR / f"failed_leads_{datetime.date.today()}.txt"

    for i, raw in enumerate(lines, 1):
        print(f"  [{i}/{total}] {raw[:60]}")
        try:
            profile, push_result = run_single(raw, no_push=no_push, source=f"batch:{filepath}")
            if push_result in ("created", "skipped"):
                results["created"] += 1
            elif push_result == "duplicate":
                results["duplicate"] += 1
            elif push_result == "portfolio":
                results["portfolio"] += 1
            csv_rows.append({
                "name": profile.name,
                "website": profile.website,
                "stage": profile.stage,
                "country": profile.country,
                "thesis_score": profile.thesis.score if profile.thesis else "",
                "contact_email": profile.contact.email if profile.contact else "",
                "contact_confidence": profile.contact.confidence if profile.contact else "",
                "push_result": push_result,
                "date": profile.date_found,
            })
        except Exception as exc:
            logger.error(f"  ❌  Failed: {raw[:60]} — {exc}")
            results["failed"] += 1
            with open(failed_log, "a") as f:
                f.write(f"{raw}\n")

    # CSV backup
    csv_path = config.TMP_DIR / f"batch_{datetime.date.today()}.csv"
    if csv_rows:
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\n  💾  CSV saved: {csv_path}")

    label = "Processed" if no_push else "Created"
    print(f"\n{'─'*40}")
    print(f"  Batch complete: {total} leads")
    print(f"  {label}:     {results['created']}")
    print(f"  Duplicate:  {results['duplicate']}")
    print(f"  Portfolio:  {results['portfolio']}")
    print(f"  Failed:     {results['failed']}")
    print(f"{'─'*40}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    _init_calibration()

    parser = argparse.ArgumentParser(
        description="Carica Scout — on-demand lead enrichment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", nargs="?", help="Company name or URL to enrich")
    parser.add_argument("--inbound", action="store_true", help="Paste mode (multi-line input)")
    parser.add_argument("--batch", metavar="FILE", help="Batch file (one name/URL per line)")
    parser.add_argument("--no-push", action="store_true", help="Enrich without pushing to Notion")
    parser.add_argument(
        "--outreach", action="store_true",
        help="Generate a first-touch outreach email draft (analyst must review before sending)"
    )
    parser.add_argument(
        "--brief", action="store_true",
        help="Generate a pre-meeting analyst brief"
    )

    args = parser.parse_args()

    if args.inbound:
        run_inbound(no_push=args.no_push)
    elif args.batch:
        run_batch(args.batch, no_push=args.no_push)
    elif args.input:
        profile, _ = run_single(args.input, no_push=args.no_push)

        if args.outreach:
            from tools.outreach import generate_outreach
            print(generate_outreach(profile))

        if args.brief:
            from tools.briefing import generate_briefing
            print(generate_briefing(profile))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
