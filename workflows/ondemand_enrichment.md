# Workflow A — On-Demand Enrichment
**Last updated:** 2026-03-10

## Objective
Given any input from the analyst (company name, URL, or raw text), produce a complete structured profile and push it to Notion.

## Trigger
Analyst runs:
```bash
python enrich.py "Company Name"
python enrich.py "https://company.com"
python enrich.py --no-push "Company Name"   # dry run
```

## Required inputs
- One of: company name, URL, or free-form text describing a startup

## Sequence

1. **Determine input type**
   - URL → fetch page content before passing to Claude
   - Company name → pass directly to `enrich_with_claude()`
   - Raw text → pass directly to `enrich_with_claude()`

2. **Run `enrichment/engine.py:enrich_with_claude(raw_input)`**
   - Claude extracts: name, website, one-liner, sector, stage, country, founders
   - Returns structured `CompanyProfile`

3. **Run `geo_score(founder)` for each founder**
   - Scores 0–4 across: university, phone prefix, LinkedIn location, company HQ
   - 2+ signals = likely CA/DR; 0–1 = uncertain

4. **Run `thesis_score(profile)`**
   - Returns score 1–5 + written rationale sentence
   - Score 5: CA/DR founder + tech + MVP + traction
   - Score 1: weak signal → flag for manual review

5. **Run `find_contact(company, founder)`**
   - Priority: scraped email → Hunter.io → constructed pattern → LinkedIn DM
   - Confidence levels: High / Medium / Unverified / N/A / ⚠️ Generic / ⚠️ Manual

6. **Portfolio check**
   - If company name matches any in `config.PORTFOLIO_COMPANIES` → skip silently

7. **Run `notion/writer.py:push_lead(profile)`**
   - New company → create page, Status = "New 🆕"
   - Duplicate → skip silently, log it
   - Schema mismatch → stop immediately, report exact field

8. **Print profile to terminal**
   - Full structured summary including all founders, geo scores, thesis score + rationale, contact

## Expected output
- Enriched profile page in Notion (unless `--no-push`)
- Terminal summary

## Edge cases

| Situation | Handling |
|---|---|
| URL unreachable | Pass company name/domain to Claude directly |
| Claude returns invalid JSON | Raise `ValueError` with raw response for debugging |
| No founders found | Continue — flag in notes, set thesis score to 1 |
| Only generic email found | Set confidence = "⚠️ Generic", flag clearly |
| Nothing found at all | Set confidence = "⚠️ Manual", analyst researches |
| Auth error (Notion/Anthropic) | Stop immediately, print missing key message |
| Portfolio company | Skip silently, log |

## Notes
- Spanish company names and content are handled natively — no translation needed
- Low online presence is common in CA/DR — low score ≠ bad company
- Always include rationale sentence, never stars alone
