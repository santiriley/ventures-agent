# Workflow B — Weekly Monitor
**Last updated:** 2026-03-10

## Objective
Every Monday, automatically scan accelerator batch pages, portfolio founder networks, and event calendars for new companies that match the Carica VC thesis. Enrich candidates and push to Notion.

## Trigger
- **Automatic:** GitHub Actions cron every Monday 07:00 Costa Rica time (UTC-6) = 13:00 UTC
- **Manual:** `python scout.py`

## Cron schedule
```
0 13 * * 1   # Monday 13:00 UTC = 07:00 Costa Rica (UTC-6)
```

## Required inputs
- Configured source URLs in `config.py`:
  - `ACCELERATOR_BATCH_URLS` — accelerator batch/portfolio pages
  - `NETWORK_PROFILE_URLS` — founder network / LinkedIn profiles
  - `EVENT_CALENDAR_URLS` — startup event calendars

## Sequence

1. **Load caches**
   - `monitor/batches.py` loads `.tmp/batches_cache.json`
   - Caches store content fingerprints to detect new entries

2. **`monitor/batches.py` — Scan accelerator batch pages**
   - Fetch each URL in `ACCELERATOR_BATCH_URLS`
   - Compare fingerprint to cache
   - Return pages with new content

3. **`monitor/network.py` — Scan portfolio networks**
   - Fetch each URL in `NETWORK_PROFILE_URLS`
   - Use Claude (fast/cheap Haiku model) to extract company mentions
   - Exclude portfolio companies from output

4. **Combine all candidates**
   - Batch page text + network mentions → list of raw inputs for enrichment

5. **Enrich each candidate**
   - For each: `enrich_with_claude()` → `geo_score()` → `thesis_score()` → `find_contact()`
   - Skip portfolio matches before pushing

6. **Push to Notion**
   - `notion/writer.py:push_lead()` for each enriched profile
   - Deduplication handled by writer

7. **`monitor/events.py` — Scan event calendars**
   - Fetch `EVENT_CALENDAR_URLS`
   - Filter for CA/DR-relevant events
   - Push to `NOTION_DB_EVENTS` if configured

8. **Save caches + CSV backup**
   - Update `.tmp/batches_cache.json`
   - Save `.tmp/weekly_leads_{date}.csv` with all new leads

9. **Log run summary**
   - Print to stdout + append to `scout.log`
   - Summary includes: mentions / candidates / added / skipped / failed

## Expected output
- New lead pages in Notion (`NOTION_DB_LEADS`), Status = "New 🆕"
- Event pages in Notion (`NOTION_DB_EVENTS`) if configured
- `.tmp/weekly_leads_{date}.csv` — CSV backup of new leads
- `scout.log` — appended run log
- `.tmp/failed_leads_{date}.txt` — any inputs that failed enrichment

## Edge cases

| Situation | Handling |
|---|---|
| Auth error (Anthropic or Notion) | Stop run immediately, log error |
| URL unreachable | Log warning, skip that source, continue |
| No new content on a batch page | Skip (fingerprint unchanged) |
| Claude rate limit | Wait and retry up to `config.MAX_RETRIES` times |
| Zero candidates found | Log summary with 0 counts, no error |
| Paid API calls needed after error | Check with analyst before re-running |

## Cost estimate
- Anthropic API: ~$5–15/month depending on candidate volume
- Hunter.io: free tier
- GitHub Actions: free tier
- Notion: free tier

## Notes
- Accelerator monitoring misses offline programs — this complements the analyst's network
- Update `ACCELERATOR_BATCH_URLS` and `NETWORK_PROFILE_URLS` in `config.py` as new sources are discovered
- If a source consistently yields no results after 4 weeks, consider removing it
- Rate limits: respect `config.REQUEST_DELAY` between requests
