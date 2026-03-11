# Workflow D — Batch Enrichment
**Last updated:** 2026-03-10

## Objective
Process a list of companies or URLs from a file, enriching each one and pushing all results to Notion.

## Trigger
```bash
python enrich.py --batch leads.txt
python enrich.py --batch leads.txt --no-push   # dry run
```

## Required inputs
- A text file (`leads.txt` or any `.txt` filename) with one company name or URL per line
- Blank lines and lines starting with `#` are skipped (use `#` for comments)

### Example file format
```
# Batch from event — Startup Summit Guatemala 2026-03
Fintech Guatemala S.A.
https://example-startup.com
AgroTech Honduras
# Below added from WhatsApp group
Startup Name CR
```

## Sequence

1. **Read file**
   - Load all non-blank, non-comment lines

2. **For each line, run Workflow A (On-Demand Enrichment)**
   - `enrich_with_claude()` → `geo_score()` → `thesis_score()` → `find_contact()`
   - Portfolio check → `push_lead()`

3. **Print progress**
   - Show `[N/total]` counter before each enrichment

4. **Log failures**
   - Any failed enrichment → append to `.tmp/failed_leads_{date}.txt`
   - Continue processing remaining leads (don't stop on individual failure)

5. **CSV backup**
   - After all leads processed, save `.tmp/batch_{date}.csv`

6. **Print summary on completion**
   - Total / created / failed

## Expected output
- All enriched profiles in Notion
- `.tmp/batch_{date}.csv` — CSV backup
- `.tmp/failed_leads_{date}.txt` — failed inputs (if any)
- Terminal summary

## Edge cases

| Situation | Handling |
|---|---|
| File not found | Exit with clear error message |
| Empty file | Exit with "No leads found" |
| Individual lead fails | Log to failed_leads, continue |
| Auth error (Anthropic or Notion) | Stop run, report |
| Duplicate encountered | Skip silently, continue |
| Portfolio company encountered | Skip silently, continue |

## Notes
- No rate limit pauses between enrichments by default; Claude API handles burst
- For very large batches (100+), consider running overnight to avoid rate limits
- Review `.tmp/failed_leads_{date}.txt` after each batch run to manually investigate failures
- Batch source is recorded in Notion as `batch:{filename}` for traceability
