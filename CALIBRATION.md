# Agent Calibration
_Last updated: 2026-03-17 | Leads reviewed: 0 | Approved by: analyst_

This file is the persistent memory of the Carica Scout agent. It is read at startup by `enrich.py`
and `scout.py` to filter, score, and source leads more accurately based on past analyst decisions.

**Auto-managed sections** (marked with comment markers) are updated by `feedback.py` automatically.
**Manually editable content** outside the comment markers is never overwritten.

---

## Known False Positives
Companies to skip before enrichment. Auto-updated after each weekly run — `size_or_stage` passes
are appended here automatically without analyst action.
<!-- feedback.py:false_positives:start -->
- Coinbase
- Nubank
- Rappi
- Mercado Libre
- Kavak
- Clip
- Konfio
- Clara
- Stripe
- Revolut
- Wise
- Brex
- Neon
- Creditas
<!-- feedback.py:false_positives:end -->

---

## Founding Year Filter
Deprioritize companies founded before this year unless strong early-stage signals are present
(recent pivot, new product line, pre-revenue confirmed). Claude will flag these in the Notes field.
<!-- feedback.py:founding_year:start -->
Current threshold: 2021
<!-- feedback.py:founding_year:end -->

---

## Raise Amount Filter
Deprioritize leads where total known funding exceeds this amount. Claude will flag these in Notes.
<!-- feedback.py:raise_threshold:start -->
Current threshold: $3M USD
<!-- feedback.py:raise_threshold:end -->

---

## Sector Adjustments
Deterministic score adjustments applied after `thesis_score()` in `engine.py`.
Format per line: `- Sector | geo_filter | delta | condition | reason`
- `geo_filter`: "outside-ca-dr" applies adjustment only when geo_score < 2
- `delta`: integer added to base score, clamped to 1–5

**Requires `python feedback.py --approve` to update.**
<!-- feedback.py:sector_adjustments:start -->
- FinTech | outside-ca-dr | -1 | geo_score < 2 | historically overscored; most are non-regional FinTechs
<!-- feedback.py:sector_adjustments:end -->

---

## Query Refinements
Extra search terms appended to Tavily queries at runtime to reduce noise from specific sources.
Format: `- tag: add terms "terms" — reason`

**Requires `python feedback.py --approve` to update.**
<!-- feedback.py:query_refinements:start -->
(No refinements yet)
<!-- feedback.py:query_refinements:end -->

---

## Source Quality
Auto-computed by `feedback.py`. Reference only — not read by the agent at runtime.
Run `python feedback.py` to populate this section.
<!-- feedback.py:source_quality:start -->
(No data yet — run `python feedback.py` to populate)
<!-- feedback.py:source_quality:end -->

---

## Open Flags for Manual Review
Unclear passes requiring analyst judgment before next calibration update.
<!-- feedback.py:open_flags:start -->
(None yet)
<!-- feedback.py:open_flags:end -->

---

## How to use

```bash
# Generate feedback report + proposed calibration updates
python feedback.py

# Review the report
cat .tmp/feedback_YYYY-MM-DD.md

# Approve judgment-call changes (sector adjustments, query refinements)
python feedback.py --approve

# The false positives section above is auto-updated every Monday by scout.py
```
