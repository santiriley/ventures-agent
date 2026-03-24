"""
scout.py — Weekly run orchestrator for Carica Scout (Workflow B).

Runs automatically every Monday 7:00am Costa Rica time via GitHub Actions.
Can also be triggered manually: python scout.py

Sequence:
  1. Load caches + calibration
  2. monitor/batches.py  → scan accelerator batch pages
  3. monitor/network.py  → scan portfolio networks
  4. Claude filters mentions
  5. Geo pre-screen (no API cost — skips non-CA/DR before enrichment)
  6. Enrich each candidate
  7. Push to Notion
  8. monitor/events.py   → scan event calendars
  9. Save caches + CSV backup
 10. Log run summary
 11. Auto-apply safe calibration updates (size/stage false positives)
"""

from __future__ import annotations

import csv
import datetime
import logging
import subprocess
import sys
from pathlib import Path

import config
from tools.notify import send_run_summary
from enrichment.engine import enrich_with_claude, load_calibration
from monitor.batches import scan_batches, scan_tavily_queries, extract_company_names, geo_prescreen, stage_prescreen, funding_precheck
from monitor.disruption import research_disruption_trends
from monitor.network import scan_network
from monitor.events import scan_events, push_events_to_notion
from notion.writer import push_lead, push_market_intel, push_disruption_memo, already_in_notion

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

    # ── Load calibration ──────────────────────────────────────────────────────
    calibration = load_calibration()

    # ── Step 0: Disruption intelligence research ──────────────────────────────
    # Runs 2-3 Tavily basic searches + 1 Claude Sonnet call to generate a
    # market memo and dynamic queries for this week's Tavily scan.
    # Fails gracefully — never blocks the rest of the run.
    logger.info("Step 0 — Researching disruption trends...")
    disruption = research_disruption_trends(dry_run=dry_run)
    dynamic_queries = disruption.get("queries", [])
    logger.info(f"  {len(dynamic_queries)} dynamic disruption queries generated.")

    if disruption.get("memo_text") and not dry_run:
        try:
            push_market_intel(
                memo_text=disruption["memo_text"],
                run_date=run_date,
                queries=dynamic_queries,
            )
        except Exception as exc:
            logger.warning(f"  Disruption memo Notion push failed (run continues): {exc}")
    elif dry_run and disruption.get("memo_text"):
        logger.info("  [dry-run] Disruption memo generated but not pushed to Notion.")

    # ── Step 0b: Push structured disruption themes to NOTION_DB_DISRUPTION ───
    themes = disruption.get("themes", [])
    if themes and not dry_run:
        logger.info(f"Step 0b — Pushing {len(themes)} disruption theme(s) to Notion...")
        disruption_stats = {"created": 0, "updated": 0, "failed": 0, "skipped": 0}
        for theme in themes:
            try:
                result = push_disruption_memo(theme, run_date=run_date)
                disruption_stats[result] = disruption_stats.get(result, 0) + 1
            except EnvironmentError as exc:
                logger.error(f"  ❌ Auth error during disruption push — stopping run: {exc}")
                _print_summary(stats, run_date, failed=True)
                sys.exit(1)
            except Exception as exc:
                logger.warning(f"  Disruption theme push failed: {exc}")
                disruption_stats["failed"] += 1
        logger.info(
            f"  Disruption themes: {disruption_stats['created']} created, "
            f"{disruption_stats['updated']} updated, "
            f"{disruption_stats['failed']} failed, "
            f"{disruption_stats['skipped']} skipped (no DB configured)."
        )
    elif themes and dry_run:
        logger.info(f"  [dry-run] {len(themes)} disruption theme(s) generated but not pushed.")
    elif not themes:
        logger.info("Step 0b — No disruption themes to push.")

    stats = {
        "mentions_found": 0,
        "candidates": 0,
        "skipped_no_geo": 0,
        "skipped_late_stage_snippet": 0,
        "skipped_false_positive": 0,
        "skipped_late_stage_precheck": 0,
        "added": 0,
        "skipped_duplicate": 0,
        "skipped_portfolio": 0,
        "skipped_stage": 0,
        "failed": 0,
    }

    csv_rows: list[dict] = []
    failed_log = config.TMP_DIR / f"failed_leads_{run_date}.txt"

    # ── Step 1: Scan accelerator batch pages + Tavily queries ─────────────────
    logger.info("Step 1 — Scanning accelerator batch pages...")
    batch_items = scan_batches()  # list[tuple[str, str]] (text, source_tag)
    logger.info(f"  {len(batch_items)} batch page(s) with new content.")

    logger.info("Step 1b — Running Tavily monitor queries (F6S, ProductHunt, Dealroom + portfolio-informed)...")
    tavily_items = scan_tavily_queries(
        query_refinements=calibration.get("query_refinements"),
        extra_queries=dynamic_queries,
    )  # list[tuple[str, str]] (text, source_tag)
    logger.info(f"  {len(tavily_items)} Tavily query result(s) added.")

    all_items = batch_items + tavily_items

    # ── Step 2: Scan portfolio networks ──────────────────────────────────────
    logger.info("Step 2 — Scanning portfolio networks...")
    network_mentions = scan_network()
    logger.info(f"  {len(network_mentions)} mention(s) from network scan.")

    # ── Step 3: Extract names + geo pre-screen ────────────────────────────────
    candidates: list[tuple[str, str]] = []  # (raw_input, source_tag)
    seen_names: set[str] = set()

    logger.info("  Extracting company names from batch pages...")
    for text, source_tag in all_items:
        name_snippets = extract_company_names(text)  # list[tuple[str, str]]
        logger.info(f"    → {len(name_snippets)} companies extracted from {source_tag}")
        for name, snippet in name_snippets:
            norm = name.lower().strip()
            if not norm or norm in seen_names:
                continue
            seen_names.add(norm)

            if not geo_prescreen(name, snippet):
                stats["skipped_no_geo"] += 1
                logger.info(f"    ⏭  {name} — no CA/DR geo signal in source")
                continue

            if not stage_prescreen(name, snippet):
                stats["skipped_late_stage_snippet"] += 1
                logger.info(f"    ⏭  {name} — late-stage signal in source snippet")
                continue

            candidates.append((name, source_tag))

    # Network mentions carry their own context — use full snippet for geo screen
    for mention in network_mentions:
        name = mention.get("name", "")
        snippet = mention.get("snippet", "")
        raw = snippet or name
        if not raw:
            continue
        norm = name.lower().strip() if name else raw.lower()[:40]
        if norm in seen_names:
            continue
        seen_names.add(norm)
        network_tag = config.NETWORK_URL_TAGS.get(
            "https://carao.com/portfolio", "network:carao"
        )
        candidates.append((raw, network_tag))

    stats["mentions_found"] = len(candidates) + stats["skipped_no_geo"]
    stats["candidates"] = len(candidates)

    logger.info(f"  {stats['skipped_no_geo']} candidate(s) skipped (no CA/DR geo signal)")
    logger.info(f"Total candidates to enrich: {len(candidates)}")

    # ── Step 4: Enrich each candidate ────────────────────────────────────────
    logger.info("Step 4 — Enriching candidates...")
    false_positives = set(calibration.get("false_positives", []))

    for i, (raw, source_tag) in enumerate(candidates, 1):
        logger.info(f"  [{i}/{len(candidates)}] Enriching: {raw[:60]}...")

        # False positive check (before any API spend)
        fp_key = raw.strip().lower().split("\n")[0][:80]
        for suffix in config.LEGAL_SUFFIXES:
            if fp_key.endswith(suffix):
                fp_key = fp_key[:-len(suffix)].strip()
        if fp_key in false_positives:
            stats["skipped_false_positive"] += 1
            logger.info(f"    ⏭  Known false positive: {raw[:60]}")
            continue

        # Funding precheck — 1 cheap Tavily search before the full 5-search enrichment
        skip_reason = funding_precheck(raw.strip().split("\n")[0][:80])
        if skip_reason:
            stats["skipped_late_stage_precheck"] += 1
            logger.info(f"    ⏭  {raw[:60]} — {skip_reason}")
            continue

        # Pre-enrichment dedup: skip Opus call if already in Notion
        # In dry-run mode, log but continue enriching so the full profile is still shown
        candidate_name = raw.strip().split("\n")[0][:80]
        if already_in_notion(candidate_name):
            if dry_run:
                logger.info(f"    ℹ️  [dry-run] {raw[:60]} — already in Notion (would skip)")
            else:
                stats["skipped_dup"] += 1
                logger.info(f"    ⏭  {raw[:60]} — already in Notion (pre-enrichment check)")
                continue

        try:
            profile = enrich_with_claude(
                raw,
                source=f"weekly_monitor:{source_tag}",
                calibration=calibration,
            )
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
                    "source": f"weekly_monitor:{source_tag}",
                })
            elif result == "duplicate":
                stats["skipped_duplicate"] += 1
                logger.info(f"    ⏭️  Duplicate: {profile.name}")
            elif result == "portfolio":
                stats["skipped_portfolio"] += 1
                logger.info(f"    🚫 Portfolio: {profile.name}")
            elif result == "stage_blocked":
                stats["skipped_stage"] += 1
                logger.info(f"    🚫 Stage-blocked: {profile.name} ({profile.stage})")
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

    # ── Step 5: Scan events ───────────────────────────────────────────────────
    logger.info("Step 5 — Scanning event calendars...")
    events = scan_events()
    events_pushed = push_events_to_notion(events)
    logger.info(f"  {events_pushed} event(s) pushed to Notion.")

    # ── Step 6: CSV backup ────────────────────────────────────────────────────
    csv_path = config.TMP_DIR / f"weekly_leads_{run_date}.csv"
    if csv_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        logger.info(f"  💾 CSV backup saved: {csv_path}")

    # ── Step 7: Summary ───────────────────────────────────────────────────────
    _print_summary(stats, run_date)

    # ── Step 8: Auto-apply calibration (safe changes only) ───────────────────
    if not dry_run:
        logger.info("Step 8 — Auto-applying calibration updates (size/stage false positives)...")
        try:
            result = subprocess.run(
                [sys.executable, str(config.ROOT / "feedback.py"), "--auto-apply"],
                capture_output=True,
                text=True,
                cwd=str(config.ROOT),
            )
            if result.stdout:
                for line in result.stdout.strip().splitlines():
                    logger.info(f"  {line}")
            if result.returncode != 0 and result.stderr:
                logger.warning(f"  feedback.py --auto-apply warning: {result.stderr.strip()[:200]}")
        except FileNotFoundError:
            logger.info("  feedback.py not found — skipping calibration auto-apply.")
        except Exception as exc:
            logger.warning(f"  Calibration auto-apply skipped: {exc}")


def _print_summary(stats: dict, run_date: str, failed: bool = False) -> None:
    logger.info("")
    logger.info("=" * 40)
    logger.info(f"Weekly Monitor — Run Summary ({run_date})")
    logger.info("=" * 40)
    logger.info(f"  Mentions found:        {stats['mentions_found']}")
    logger.info(f"  Skipped (no geo):      {stats.get('skipped_no_geo', 0)}")
    logger.info(f"  Skipped (late snippet):{stats.get('skipped_late_stage_snippet', 0)}")
    logger.info(f"  Candidates:            {stats['candidates']}")
    logger.info(f"  Skipped (false pos):   {stats.get('skipped_false_positive', 0)}")
    logger.info(f"  Skipped (precheck):    {stats.get('skipped_late_stage_precheck', 0)}")
    logger.info(f"  Added to Notion:       {stats['added']}")
    logger.info(f"  Skipped (dup):         {stats['skipped_duplicate']}")
    logger.info(f"  Skipped (portf.):      {stats['skipped_portfolio']}")
    logger.info(f"  Skipped (stage gate):  {stats.get('skipped_stage', 0)}")
    logger.info(f"  Failed:                {stats['failed']}")
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
