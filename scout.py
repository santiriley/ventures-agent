"""
scout.py — Weekly run orchestrator for Carica Scout (Workflow B).

Runs automatically every Monday 7:00am Costa Rica time via GitHub Actions.
Can also be triggered manually: python scout.py

Sequence:
  1. Load caches
  2. monitor/batches.py  → scan accelerator batch pages
  3. monitor/network.py  → scan portfolio networks
  4. Claude filters mentions
  5. Enrich each candidate
  6. Push to Notion
  7. monitor/events.py   → scan event calendars
  8. Save caches + CSV backup
  9. Log run summary
"""

from __future__ import annotations

import csv
import datetime
import logging
import sys
from pathlib import Path

import config
from tools.notify import send_run_summary
from enrichment.engine import enrich_with_claude
from monitor.batches import scan_batches, scan_tavily_queries, extract_company_names
from monitor.network import scan_network
from monitor.events import scan_events, push_events_to_notion
from notion.writer import push_lead

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = config.ROOT / "scout.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run_weekly_monitor(dry_run: bool = False) -> None:
    run_date = datetime.date.today().isoformat()
    logger.info(f"=== Carica Scout — Weekly Monitor ===")
    logger.info(f"=== Run date: {run_date} ===")
    if dry_run:
        logger.info("=== DRY RUN — Notion pushes disabled ===")

    stats = {
        "mentions_found": 0,
        "candidates": 0,
        "added": 0,
        "skipped_duplicate": 0,
        "skipped_portfolio": 0,
        "failed": 0,
    }

    csv_rows: list[dict] = []
    failed_log = config.TMP_DIR / f"failed_leads_{run_date}.txt"

    # ── Step 1: Scan accelerator batch pages + Tavily queries ─────────────────
    logger.info("Step 1 — Scanning accelerator batch pages...")
    batch_texts = scan_batches()
    logger.info(f"  {len(batch_texts)} batch page(s) with new content.")

    logger.info("Step 1b — Running Tavily monitor queries (F6S, ProductHunt, Dealroom)...")
    tavily_texts = scan_tavily_queries()
    batch_texts.extend(tavily_texts)
    logger.info(f"  {len(tavily_texts)} Tavily query result(s) added.")

    # ── Step 2: Scan portfolio networks ──────────────────────────────────────
    logger.info("Step 2 — Scanning portfolio networks...")
    network_mentions = scan_network()
    logger.info(f"  {len(network_mentions)} mention(s) from network scan.")

    # Combine: extract individual company names from batch pages; network mentions carry name+snippet
    candidates: list[str] = []
    seen_names: set[str] = set()

    logger.info("  Extracting company names from batch pages...")
    for text in batch_texts:
        names = extract_company_names(text)
        logger.info(f"    → {len(names)} companies extracted")
        for name in names:
            norm = name.lower().strip()
            if norm and norm not in seen_names:
                seen_names.add(norm)
                candidates.append(name)

    for mention in network_mentions:
        raw = mention.get("snippet") or mention.get("name") or ""
        if raw:
            candidates.append(raw)

    stats["mentions_found"] = len(candidates)
    stats["candidates"] = len(candidates)

    logger.info(f"Total candidates to enrich: {len(candidates)}")

    # ── Step 3: Enrich each candidate ────────────────────────────────────────
    logger.info("Step 3 — Enriching candidates...")

    for i, raw in enumerate(candidates, 1):
        logger.info(f"  [{i}/{len(candidates)}] Enriching: {raw[:60]}...")
        try:
            profile = enrich_with_claude(raw, source="weekly_monitor")
            profile.date_found = run_date

            result = "skipped_dry_run" if dry_run else push_lead(profile)

            if result == "created":
                stats["added"] += 1
                logger.info(f"    ✅ Added: {profile.name}")
                csv_rows.append({
                    "name": profile.name,
                    "website": profile.website,
                    "stage": profile.stage,
                    "country": profile.country,
                    "thesis_score": profile.thesis.score if profile.thesis else "",
                    "thesis_rationale": profile.thesis.rationale if profile.thesis else "",
                    "contact_email": profile.contact.email if profile.contact else "",
                    "contact_confidence": profile.contact.confidence if profile.contact else "",
                    "date": run_date,
                    "source": "weekly_monitor",
                })
            elif result == "duplicate":
                stats["skipped_duplicate"] += 1
                logger.info(f"    ⏭️  Duplicate: {profile.name}")
            elif result == "portfolio":
                stats["skipped_portfolio"] += 1
                logger.info(f"    🚫 Portfolio: {profile.name}")
            elif result == "skipped_dry_run":
                stats["added"] += 1   # count as "would have been added"
                logger.info(f"    🔎 [dry-run] Would push: {profile.name}")

        except EnvironmentError as exc:
            # Auth failure — stop immediately
            logger.error(f"  ❌ Auth error — stopping run: {exc}")
            _print_summary(stats, run_date, failed=True)
            sys.exit(1)
        except Exception as exc:
            stats["failed"] += 1
            logger.error(f"  ❌ Failed: {raw[:60]} — {exc}")
            with open(failed_log, "a") as f:
                f.write(f"{raw[:200]}\n")

    # ── Step 4: Scan events ───────────────────────────────────────────────────
    logger.info("Step 4 — Scanning event calendars...")
    events = scan_events()
    events_pushed = push_events_to_notion(events)
    logger.info(f"  {events_pushed} event(s) pushed to Notion.")

    # ── Step 5: CSV backup ────────────────────────────────────────────────────
    csv_path = config.TMP_DIR / f"weekly_leads_{run_date}.csv"
    if csv_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        logger.info(f"  💾 CSV backup saved: {csv_path}")

    # ── Step 6: Summary ───────────────────────────────────────────────────────
    _print_summary(stats, run_date)


def _print_summary(stats: dict, run_date: str, failed: bool = False) -> None:
    logger.info("")
    logger.info("=" * 40)
    logger.info(f"Weekly Monitor — Run Summary ({run_date})")
    logger.info("=" * 40)
    logger.info(f"  Mentions found:     {stats['mentions_found']}")
    logger.info(f"  Candidates:         {stats['candidates']}")
    logger.info(f"  Added to Notion:    {stats['added']}")
    logger.info(f"  Skipped (dup):      {stats['skipped_duplicate']}")
    logger.info(f"  Skipped (portf.):   {stats['skipped_portfolio']}")
    logger.info(f"  Failed:             {stats['failed']}")
    logger.info("=" * 40)
    logger.info("")

    try:
        send_run_summary(stats, run_date, failed=failed)
    except Exception as exc:
        logger.warning(f"  ⚠️  Email notification failed (run not affected): {exc}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Carica Scout — weekly monitor")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run all steps but skip Notion pushes (safe for testing)"
    )
    args = parser.parse_args()
    run_weekly_monitor(dry_run=args.dry_run)
