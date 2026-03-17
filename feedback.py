"""
feedback.py — Feedback and calibration tool for Carica Scout.

Reads Notion outcomes (Passed ❌ and Portfolio ✅), classifies each pass,
computes source quality scores, and updates CALIBRATION.md.

The loop:
  1. Analyst marks leads as Passed ❌ or Portfolio ✅ in Notion
  2. feedback.py classifies outcomes and auto-applies safe changes
  3. Analyst reviews report + approves judgment calls
  4. Next run uses updated calibration → better sourcing and scoring

Usage:
  python feedback.py               # full run: classify, analyze, auto-apply, write files
  python feedback.py --dry-run     # print report only, no file writes
  python feedback.py --approve     # merge calibration_draft_{date}.md → CALIBRATION.md
  python feedback.py --auto-apply  # apply only size_or_stage false positives (called by scout.py)
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from collections import Counter
from pathlib import Path

import requests

import config

# ── Constants ─────────────────────────────────────────────────────────────────

CALIBRATION_FILE = config.ROOT / "CALIBRATION.md"

# Keywords that signal a company is too large/mature for our thesis
SIZE_STAGE_KEYWORDS = [
    "series c", "series d", "series e", "series f",
    "unicorn", "public", "ipo", "listed",
    "too large", "already raised", "grown too large",
    "$50m", "$100m", "$200m", "$500m", "$1b", "$1 b",
    "50 million", "100 million", "200 million",
    "founded in 2015", "founded in 2016", "founded in 2017", "founded in 2018",
    "founded in 201", "founded in 200", "founded in 199",
    "founded before 2019",
]

SIZE_STAGE_KNOWN_NAMES: set[str] = {
    "coinbase", "nubank", "rappi", "mercado libre", "kavak",
    "clip", "konfio", "clara", "stripe", "revolut", "wise",
    "brex", "neon", "creditas", "loft", "quinto andar",
    "ifood", "loggi", "gympass", "rappi", "mercadopago",
}


# ── Notion helpers ─────────────────────────────────────────────────────────────

def _notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.get_key('NOTION_API_KEY')}",
        "Notion-Version": config.NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def _fetch_notion_outcomes() -> list[dict]:
    """Fetch all Passed ❌ and Portfolio ✅ leads from Notion with pagination."""
    db_id = config.get_key("NOTION_DB_LEADS")
    url = f"{config.NOTION_BASE_URL}/databases/{db_id}/query"
    headers = _notion_headers()

    body: dict = {
        "filter": {
            "or": [
                {"property": "Status", "select": {"equals": "Passed ❌"}},
                {"property": "Status", "select": {"equals": "Portfolio ✅"}},
            ]
        },
        "page_size": 100,
    }

    leads = []
    cursor = None

    while True:
        if cursor:
            body["start_cursor"] = cursor

        resp = requests.post(url, headers=headers, json=body, timeout=config.REQUEST_TIMEOUT)

        if resp.status_code == 401:
            print("❌  Notion auth failed — check NOTION_API_KEY.")
            raise EnvironmentError("Notion auth failed")
        resp.raise_for_status()

        data = resp.json()
        for page in data.get("results", []):
            lead = _extract_lead(page)
            if lead:
                leads.append(lead)

        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break

    return leads


def _extract_lead(page: dict) -> dict | None:
    """Extract relevant fields from a Notion page object."""
    props = page.get("properties", {})

    def get_title(field: str) -> str:
        items = (props.get(field) or {}).get("title", [])
        return items[0]["text"]["content"] if items else ""

    def get_select(field: str) -> str:
        sel = (props.get(field) or {}).get("select")
        return sel["name"] if sel else ""

    def get_text(field: str) -> str:
        items = (props.get(field) or {}).get("rich_text", [])
        return items[0]["text"]["content"] if items else ""

    def get_number(field: str):
        return (props.get(field) or {}).get("number")

    def get_date(field: str) -> str:
        d = (props.get(field) or {}).get("date")
        return d["start"] if d else ""

    name = get_title("Name")
    if not name:
        return None

    return {
        "name": name,
        "status": get_select("Status"),
        "sector": get_select("Sector"),
        "stage": get_select("Stage"),
        "country": get_select("Country"),
        "thesis_score": get_number("Thesis Score"),
        "thesis_rationale": get_text("Thesis Rationale"),
        "notes": get_text("Notes"),
        "source": get_text("Source"),
        "date_found": get_date("Date Found"),
    }


# ── Classification ────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    n = name.lower().strip()
    for suffix in config.LEGAL_SUFFIXES:
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    return n


def _classify_pass(lead: dict) -> str:
    """Classify a Passed ❌ lead as 'size_or_stage', 'fit', or 'unclear'."""
    text = (
        (lead.get("notes") or "") + " " + (lead.get("thesis_rationale") or "")
    ).lower()
    name_normalized = _normalize_name(lead.get("name", ""))

    if name_normalized in SIZE_STAGE_KNOWN_NAMES:
        return "size_or_stage"

    if any(kw in text for kw in SIZE_STAGE_KEYWORDS):
        return "size_or_stage"

    # Insufficient notes to classify
    if len(text.strip()) < 10:
        return "unclear"

    return "fit"


# ── Source quality ─────────────────────────────────────────────────────────────

def _compute_source_quality(leads: list[dict]) -> list[dict]:
    """Compute quality metrics per source tag."""
    source_data: dict[str, dict] = {}

    for lead in leads:
        source = lead.get("source") or "unknown"
        # Strip "weekly_monitor:" prefix for cleaner display
        tag = source.replace("weekly_monitor:", "").strip()
        if not tag:
            tag = "unknown"

        if tag not in source_data:
            source_data[tag] = {"total": 0, "fit_pass": 0, "size_or_stage": 0, "portfolio": 0}

        d = source_data[tag]
        d["total"] += 1

        if lead.get("status") == "Portfolio ✅":
            d["portfolio"] += 1
        elif lead.get("status") == "Passed ❌":
            cls = _classify_pass(lead)
            if cls == "fit":
                d["fit_pass"] += 1
            elif cls == "size_or_stage":
                d["size_or_stage"] += 1

    result = []
    for tag, d in sorted(source_data.items(), key=lambda x: -x[1]["total"]):
        total = d["total"]
        if total == 0:
            continue
        result.append({
            "tag": tag,
            "total": total,
            "fit_pass_rate": round(d["fit_pass"] / total, 2),
            "size_or_stage_rate": round(d["size_or_stage"] / total, 2),
            "portfolio_rate": round(d["portfolio"] / total, 2),
            "needs_refinement": d["fit_pass"] / total > 0.5,
        })

    return result


# ── Pattern detection ──────────────────────────────────────────────────────────

def _detect_patterns(fit_passes: list[dict]) -> list[dict]:
    """Find recurring sector/geo combos in fit-passes (3+ = systemic)."""
    combo_counter: Counter = Counter()
    combo_leads: dict[str, list[str]] = {}

    for lead in fit_passes:
        sector = (lead.get("sector") or "unknown").lower()
        country = (lead.get("country") or "unknown").lower()
        is_cadr = country in {c.lower() for c in config.CA_DR_COUNTRY_NAMES}
        geo_label = "ca-dr" if is_cadr else "outside-ca-dr"
        combo = f"{sector}|{geo_label}"
        combo_counter[combo] += 1
        combo_leads.setdefault(combo, []).append(lead["name"])

    patterns = []
    for combo, count in combo_counter.most_common():
        if count >= 3:
            sector, geo = combo.split("|", 1)
            patterns.append({
                "sector": sector,
                "geo_filter": geo,
                "count": count,
                "names": combo_leads[combo],
                "proposed_delta": -1 if geo == "outside-ca-dr" else 0,
            })

    return patterns


# ── CALIBRATION.md management ─────────────────────────────────────────────────

def _load_calibration_text() -> str:
    if CALIBRATION_FILE.exists():
        return CALIBRATION_FILE.read_text(encoding="utf-8")
    return ""


def _get_current_false_positives() -> set[str]:
    """Return the current set of normalized false positive names from CALIBRATION.md."""
    text = _load_calibration_text()
    pattern = (
        r"<!--\s*feedback\.py:false_positives:start\s*-->"
        r"(.*?)"
        r"<!--\s*feedback\.py:false_positives:end\s*-->"
    )
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return set()
    fps = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            fps.add(_normalize_name(line[2:].strip()))
    return fps


def _auto_apply_false_positives(new_names: list[str], dry_run: bool = False) -> int:
    """
    Append new false positives to CALIBRATION.md false_positives section.
    Returns count of names actually added.
    """
    if not CALIBRATION_FILE.exists():
        print("⚠️  CALIBRATION.md not found — cannot auto-apply. Create it first.")
        return 0

    current = _get_current_false_positives()
    to_add = [n for n in new_names if _normalize_name(n) not in current]

    if not to_add:
        print("  No new false positives to add.")
        return 0

    if dry_run:
        print(f"  [dry-run] Would add {len(to_add)} false positive(s): {', '.join(to_add)}")
        return len(to_add)

    text = _load_calibration_text()
    new_lines = "\n".join(f"- {name}" for name in to_add)

    pattern = (
        r"(<!--\s*feedback\.py:false_positives:start\s*-->)"
        r"(.*?)"
        r"(<!--\s*feedback\.py:false_positives:end\s*-->)"
    )

    def replacer(m: re.Match) -> str:
        existing = m.group(2).rstrip()
        return m.group(1) + existing + "\n" + new_lines + "\n" + m.group(3)

    new_text = re.sub(pattern, replacer, text, flags=re.DOTALL)

    # Update last-updated metadata
    today = datetime.date.today().isoformat()
    new_text = re.sub(r"_Last updated: [^|]+\|", f"_Last updated: {today} |", new_text)

    CALIBRATION_FILE.write_text(new_text, encoding="utf-8")
    print(f"  ✅  Auto-applied: {len(to_add)} new false positive(s) added to CALIBRATION.md")
    for name in to_add:
        print(f"      + {name}")
    return len(to_add)


def _merge_judgment_calls(draft_path: Path) -> None:
    """
    Merge judgment-call sections (sector_adjustments, query_refinements, open_flags)
    from a calibration draft into CALIBRATION.md. Manual notes outside markers are preserved.
    False positives section is intentionally NOT merged here (managed by auto-apply).
    """
    if not draft_path.exists():
        print(f"❌  Draft file not found: {draft_path}")
        sys.exit(1)

    if not CALIBRATION_FILE.exists():
        print("❌  CALIBRATION.md not found. Create it first.")
        sys.exit(1)

    draft_text = draft_path.read_text(encoding="utf-8")
    cal_text = _load_calibration_text()

    # Only merge judgment-call sections — never touch false_positives
    judgment_sections = ["sector_adjustments", "query_refinements", "sourcing_adjustments", "open_flags"]

    updated = cal_text
    merged_count = 0

    for section in judgment_sections:
        section_pattern = (
            rf"(<!--\s*feedback\.py:{re.escape(section)}:start\s*-->)"
            rf"(.*?)"
            rf"(<!--\s*feedback\.py:{re.escape(section)}:end\s*-->)"
        )

        draft_match = re.search(section_pattern, draft_text, re.DOTALL)
        if not draft_match:
            continue

        new_content = draft_match.group(2)

        def make_replacer(content: str):
            def replacer(m: re.Match) -> str:
                return m.group(1) + content + m.group(3)
            return replacer

        new_updated = re.sub(
            section_pattern, make_replacer(new_content), updated, flags=re.DOTALL
        )
        if new_updated != updated:
            merged_count += 1
        updated = new_updated

    # Update metadata
    today = datetime.date.today().isoformat()
    updated = re.sub(r"_Last updated: [^|]+\|", f"_Last updated: {today} |", updated)

    CALIBRATION_FILE.write_text(updated, encoding="utf-8")
    print(f"✅  Merged {merged_count} section(s) into CALIBRATION.md from {draft_path.name}")


def _update_source_quality_section(source_quality: list[dict]) -> None:
    """Update the Source Quality reference section in CALIBRATION.md."""
    if not CALIBRATION_FILE.exists() or not source_quality:
        return

    lines = ["\n"]
    lines.append("| Source | Total | Fit Pass Rate | Size/Stage Rate | Portfolio Rate |")
    lines.append("|---|---|---|---|---|")
    for sq in source_quality:
        flag = " ⚠️" if sq["needs_refinement"] else ""
        lines.append(
            f"| {sq['tag']}{flag} | {sq['total']} | {sq['fit_pass_rate']:.0%} | "
            f"{sq['size_or_stage_rate']:.0%} | {sq['portfolio_rate']:.0%} |"
        )
    lines.append("")

    new_content = "\n".join(lines)
    text = _load_calibration_text()
    pattern = (
        r"(<!--\s*feedback\.py:source_quality:start\s*-->)"
        r"(.*?)"
        r"(<!--\s*feedback\.py:source_quality:end\s*-->)"
    )
    new_text = re.sub(
        pattern,
        lambda m: m.group(1) + new_content + m.group(3),
        text,
        flags=re.DOTALL,
    )
    if new_text != text:
        CALIBRATION_FILE.write_text(new_text, encoding="utf-8")


# ── Report writing ────────────────────────────────────────────────────────────

def _write_feedback_report(
    leads: list[dict],
    fit_passes: list[dict],
    size_passes: list[dict],
    unclear_passes: list[dict],
    portfolio_leads: list[dict],
    patterns: list[dict],
    source_quality: list[dict],
    new_fps_added: int,
    date: str,
) -> Path:
    lines = [
        f"# Carica Scout — Feedback Report",
        f"_Generated: {date}_\n",
        "## Summary Stats",
        f"- Total reviewed: {len(leads)}",
        f"- Passed ❌: {len(fit_passes) + len(size_passes) + len(unclear_passes)}",
        f"  - Fit passes (thesis mismatch): {len(fit_passes)}",
        f"  - Size/stage passes (stale data): {len(size_passes)}",
        f"  - Unclear: {len(unclear_passes)}",
        f"- Portfolio ✅: {len(portfolio_leads)}",
        f"- False positives auto-added this run: {new_fps_added}\n",
    ]

    # Score distribution for fit passes
    if fit_passes:
        scores = [l.get("thesis_score") for l in fit_passes if l.get("thesis_score")]
        if scores:
            dist: Counter = Counter(scores)
            lines.append("### Fit-Pass Score Distribution")
            lines.append("_(High scores here = over-scoring problem)_")
            for s in sorted(dist.keys(), reverse=True):
                lines.append(f"  - Score {s}: {dist[s]} lead(s)")
            lines.append("")

    lines.append("## Fit Passes (Thesis Mismatch)")
    if fit_passes:
        for lead in fit_passes:
            lines.append(
                f"- **{lead['name']}** | {lead.get('sector', 'N/A')} | "
                f"{lead.get('country', 'N/A')} | Score: {lead.get('thesis_score', 'N/A')}"
            )
            if lead.get("notes"):
                lines.append(f"  Notes: {lead['notes'][:120]}")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Size/Stage Passes (Stale Data)")
    if size_passes:
        for lead in size_passes:
            lines.append(
                f"- **{lead['name']}** | {lead.get('sector', 'N/A')} | {lead.get('country', 'N/A')}"
            )
            lines.append(
                "  ⚠️ Possible stale data — company may have been early-stage at founding but has since grown."
            )
            lines.append("  → Auto-added to CALIBRATION.md false positives.")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Unclear Passes (Manual Review)")
    if unclear_passes:
        for lead in unclear_passes:
            lines.append(
                f"- **{lead['name']}** | Score: {lead.get('thesis_score', 'N/A')} | Notes missing or too short"
            )
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Portfolio Leads (What's Working)")
    if portfolio_leads:
        sectors: Counter = Counter(l.get("sector", "N/A") for l in portfolio_leads)
        countries: Counter = Counter(l.get("country", "N/A") for l in portfolio_leads)
        lines.append("### Sectors:")
        for sector, count in sectors.most_common(8):
            lines.append(f"  - {sector}: {count}")
        lines.append("### Countries:")
        for country, count in countries.most_common(8):
            lines.append(f"  - {country}: {count}")
    else:
        lines.append("_None yet._")
    lines.append("")

    lines.append("## Pattern Analysis")
    if patterns:
        lines.append("### Systemic sourcing problems (3+ fit-passes in same sector/geo):")
        for p in patterns:
            lines.append(f"- **{p['sector']} / {p['geo_filter']}**: {p['count']} fit-passes")
            lines.append(f"  Companies: {', '.join(p['names'][:5])}")
            if p.get("proposed_delta"):
                lines.append(f"  → Proposed calibration adjustment: score {p['proposed_delta']:+d}")
    else:
        lines.append(
            "_No systemic patterns detected yet. Patterns appear after 3+ fit-passes in the same sector/geo._"
        )
    lines.append("")

    lines.append("## Source Quality")
    if source_quality:
        lines.append("| Source | Total | Fit Pass Rate | Size/Stage Rate | Portfolio Rate | Flag |")
        lines.append("|---|---|---|---|---|---|")
        for sq in source_quality:
            flag = "⚠️ Needs refinement" if sq["needs_refinement"] else ""
            lines.append(
                f"| {sq['tag']} | {sq['total']} | {sq['fit_pass_rate']:.0%} | "
                f"{sq['size_or_stage_rate']:.0%} | {sq['portfolio_rate']:.0%} | {flag} |"
            )
        lines.append("")
        lines.append(
            "_Note: source tags require scout.py ≥ v1.2 (2026-03). "
            "Older leads show 'unknown' or 'weekly_monitor'._"
        )
    else:
        lines.append(
            "_Not enough data yet — source tags require the updated scout.py._"
        )
    lines.append("")

    if patterns or [sq for sq in source_quality if sq["needs_refinement"]]:
        lines.append(
            "_Run `python feedback.py --approve` to merge proposed calibration updates._"
        )

    path = config.TMP_DIR / f"feedback_{date}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_calibration_draft(
    patterns: list[dict],
    source_quality: list[dict],
    unclear_passes: list[dict],
    date: str,
) -> Path | None:
    """Write proposed judgment-call calibration updates to a draft file for analyst review."""
    noisy_sources = [sq for sq in source_quality if sq["needs_refinement"] and sq["tag"].startswith("tavily:")]
    has_changes = bool(patterns or noisy_sources or unclear_passes)

    if not has_changes:
        return None

    cal_text = _load_calibration_text()

    # Load existing sector adjustments to avoid duplicates
    pattern_sa = (
        r"<!--\s*feedback\.py:sector_adjustments:start\s*-->"
        r"(.*?)"
        r"<!--\s*feedback\.py:sector_adjustments:end\s*-->"
    )
    m = re.search(pattern_sa, cal_text, re.DOTALL)
    existing_adj = m.group(1).strip() if m else ""

    sector_adj_lines: list[str] = []
    for p in patterns:
        if p.get("proposed_delta"):
            line = (
                f"- {p['sector'].title()} | {p['geo_filter']} | {p['proposed_delta']} | "
                f"geo_score based | {p['count']} fit-passes detected"
            )
            if line not in existing_adj:
                sector_adj_lines.append(line)

    qr_lines: list[str] = []
    for sq in noisy_sources:
        qr_lines.append(
            f'- {sq["tag"]}: add terms "early-stage OR pre-seed OR seed" — '
            f'{sq["fit_pass_rate"]:.0%} fit-pass rate detected'
        )

    flag_lines: list[str] = []
    for lead in unclear_passes:
        flag_lines.append(
            f'- **{lead["name"]}** | Score: {lead.get("thesis_score", "N/A")} | '
            f'No notes — requires manual analyst judgment'
        )

    output_lines = [
        f"# Calibration Draft — {date}",
        f"_Generated by feedback.py — review, then run `python feedback.py --approve`_\n",
        "---\n",
        "**What this file contains:** proposed updates to CALIBRATION.md judgment-call sections.",
        "False positives were already auto-applied. Only sector adjustments, query refinements,",
        "and open flags are here for your review.\n",
    ]

    if existing_adj or sector_adj_lines:
        output_lines.append("<!-- feedback.py:sector_adjustments:start -->")
        if existing_adj:
            output_lines.append(existing_adj)
        output_lines.extend(sector_adj_lines)
        output_lines.append("<!-- feedback.py:sector_adjustments:end -->\n")

    if qr_lines:
        output_lines.append("<!-- feedback.py:query_refinements:start -->")
        output_lines.extend(qr_lines)
        output_lines.append("<!-- feedback.py:query_refinements:end -->\n")

    if flag_lines:
        output_lines.append("<!-- feedback.py:open_flags:start -->")
        output_lines.extend(flag_lines)
        output_lines.append("<!-- feedback.py:open_flags:end -->\n")

    path = config.TMP_DIR / f"calibration_draft_{date}.md"
    path.write_text("\n".join(output_lines), encoding="utf-8")
    return path


# ── Main run logic ────────────────────────────────────────────────────────────

def run(dry_run: bool = False, auto_apply_only: bool = False) -> None:
    print("Fetching Notion outcomes (Passed ❌ and Portfolio ✅)...")
    try:
        leads = _fetch_notion_outcomes()
    except EnvironmentError:
        sys.exit(1)
    except Exception as exc:
        print(f"❌  Failed to fetch Notion data: {exc}")
        sys.exit(1)

    if not leads:
        print("No Passed ❌ or Portfolio ✅ leads found in Notion yet.")
        print("Mark some leads and try again.")
        return

    print(f"  Found {len(leads)} lead(s) with outcome status.")

    # Classify
    passed_leads = [l for l in leads if l["status"] == "Passed ❌"]
    portfolio_leads = [l for l in leads if l["status"] == "Portfolio ✅"]

    fit_passes: list[dict] = []
    size_passes: list[dict] = []
    unclear_passes: list[dict] = []

    for lead in passed_leads:
        cls = _classify_pass(lead)
        if cls == "fit":
            fit_passes.append(lead)
        elif cls == "size_or_stage":
            size_passes.append(lead)
        else:
            unclear_passes.append(lead)

    # Auto-apply false positives (always safe — idempotent)
    new_fps = [lead["name"] for lead in size_passes]
    new_fps_added = _auto_apply_false_positives(new_fps, dry_run=dry_run)

    if auto_apply_only:
        return  # stop here — scout.py only needs this step

    # Source quality + pattern analysis
    source_quality = _compute_source_quality(leads)
    patterns = _detect_patterns(fit_passes)

    date = datetime.date.today().isoformat()

    if dry_run:
        _print_dry_run_report(
            leads, fit_passes, size_passes, unclear_passes,
            portfolio_leads, patterns, source_quality, date,
        )
        return

    # Write report file
    report_path = _write_feedback_report(
        leads, fit_passes, size_passes, unclear_passes,
        portfolio_leads, patterns, source_quality, new_fps_added, date,
    )
    print(f"\n  📄  Feedback report: {report_path}")

    # Write calibration draft
    draft_path = _write_calibration_draft(patterns, source_quality, unclear_passes, date)
    if draft_path:
        print(f"  📝  Calibration draft: {draft_path}")
        print(f"      Review it, then run: python feedback.py --approve")
    else:
        print("  No judgment-call changes proposed — nothing to approve yet.")

    # Update source quality reference section in CALIBRATION.md
    _update_source_quality_section(source_quality)

    print(f"\n  Summary: {len(fit_passes)} fit passes | {len(size_passes)} size/stage passes | "
          f"{len(portfolio_leads)} portfolio | {new_fps_added} false positives added")


def _print_dry_run_report(
    leads, fit_passes, size_passes, unclear_passes,
    portfolio_leads, patterns, source_quality, date,
) -> None:
    print(f"\n{'='*60}")
    print(f"FEEDBACK REPORT (dry-run) — {date}")
    print(f"{'='*60}")
    print(f"Total reviewed: {len(leads)}")
    print(f"Passed: {len(fit_passes) + len(size_passes) + len(unclear_passes)}")
    print(f"  Fit passes:       {len(fit_passes)}")
    print(f"  Size/stage:       {len(size_passes)}")
    print(f"  Unclear:          {len(unclear_passes)}")
    print(f"Portfolio:          {len(portfolio_leads)}")

    if fit_passes:
        print(f"\nFit Passes (would look for calibration opportunities):")
        for lead in fit_passes:
            print(f"  - {lead['name']} | {lead.get('sector','N/A')} | {lead.get('country','N/A')} | score {lead.get('thesis_score','N/A')}")

    if size_passes:
        print(f"\nSize/Stage Passes (would auto-add to false positives):")
        for lead in size_passes:
            print(f"  - {lead['name']}")

    if patterns:
        print(f"\nPatterns detected (3+ recurrences):")
        for p in patterns:
            print(f"  - {p['sector']} / {p['geo_filter']}: {p['count']} fit-passes")

    if source_quality:
        print(f"\nSource Quality:")
        for sq in source_quality:
            flag = " ⚠️ needs refinement" if sq["needs_refinement"] else ""
            print(
                f"  - {sq['tag']}: {sq['total']} leads, "
                f"{sq['fit_pass_rate']:.0%} fit-pass rate{flag}"
            )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carica Scout — feedback and calibration tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run", action="store_true", help="Print report only, no file writes")
    parser.add_argument(
        "--approve", action="store_true",
        help="Merge latest calibration draft into CALIBRATION.md (judgment calls only)"
    )
    parser.add_argument(
        "--auto-apply", action="store_true",
        help="Apply only size_or_stage false positives (called automatically by scout.py)"
    )
    args = parser.parse_args()

    if args.approve:
        drafts = sorted(config.TMP_DIR.glob("calibration_draft_*.md"), reverse=True)
        if not drafts:
            print("❌  No calibration draft found. Run `python feedback.py` first.")
            sys.exit(1)
        draft = drafts[0]
        print(f"Approving calibration draft: {draft.name}")
        _merge_judgment_calls(draft)
    elif getattr(args, "auto_apply"):
        run(dry_run=False, auto_apply_only=True)
    elif args.dry_run:
        run(dry_run=True)
    else:
        run(dry_run=False)


if __name__ == "__main__":
    main()
