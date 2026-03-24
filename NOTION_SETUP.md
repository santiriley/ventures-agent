# Notion Database Setup — Carica Scout

This guide walks you through creating the two Notion databases that Carica Scout
expects. Complete this once before running `enrich.py` for the first time.

---

## 1. Create the Notion Integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **+ New integration**
3. Name it `Carica Scout`
4. Set **Capabilities**: Read content · Update content · Insert content
5. Copy the **Internal Integration Token** → this is your `NOTION_API_KEY`

---

## 2. Leads Database (required)

### Create the database

1. In Notion, create a new **full-page database** (not inline)
2. Name it `Carica Leads` (or any name you prefer)
3. Copy the database ID from the URL:
   `notion.so/**{DATABASE_ID}**?v=...`
   → this is your `NOTION_DB_LEADS`

### Connect your integration

- Open the database → click `•••` (top right) → **Connections** → **Add connection** → select `Carica Scout`

### Required properties

Create exactly these properties with these types and names
(case-sensitive — the writer checks field names exactly):

| Property name      | Type          | Notes |
|-------------------|---------------|-------|
| `Name`            | Title         | Auto-created by Notion |
| `Website`         | URL           | |
| `One-liner`       | Text          | |
| `Sector`          | Select        | Values auto-created on first push |
| `Stage`           | Select        | Values: Pre-Seed · Seed · Series-A · Unknown |
| `Country`         | Select        | Values auto-created on first push |
| `Founders`        | Text          | Formatted as "Name [N geo signals]; ..." |
| `Thesis Score`    | Number        | 1–5; format as plain number |
| `Thesis Rationale`| Text          | |
| `Contact Email`   | Email         | |
| `Contact Confidence` | Select     | Values: High · Medium · Unverified · N/A · ⚠️ Generic · ⚠️ Manual |
| `Source`          | Text          | |
| `Date Found`      | Date          | |
| `Status`          | Select        | See pipeline stages below |
| `Notes`           | Text          | |

### Status pipeline stages

Add these options to the `Status` select property in order:

| Value | Meaning |
|---|---|
| `New 🆕` | Auto-set on push; not yet reviewed |
| `Reviewing 🔍` | Analyst has opened the record |
| `Contacted 📧` | First outreach sent |
| `Meeting Scheduled 📅` | Call booked |
| `Active Interest ⚡` | Post-meeting, fund wants to continue |
| `Due Diligence 🔬` | Deep dive underway |
| `IC Memo 📄` | Investment memo drafted |
| `Portfolio ✅` | Investment closed |
| `Passed ❌` | Declined; add Pass Reason in Notes |
| `Stale ⏸` | No response after 3 follow-ups |

### Additional properties (created automatically on first push)

The writer also pushes these fields — create them in Notion if you want them visible:

| Property name | Type | Notes |
|---|---|---|
| `Portfolio Fit Score` | Number | 0–4; how closely this company matches portfolio patterns |
| `Portfolio Fit Note` | Text | Explanation of the fit score |
| `Traction Signals` | Text | Concrete evidence: users, revenue, partnerships |
| `Founder Background` | Text | Relevant domain experience summary |
| `Non-CA Founder (Building in Region)` | Checkbox | True when a non-CA/DR founder is explicitly targeting the region |

### Recommended views

- **All Leads** — default table, sorted by Date Found (newest first)
- **Pipeline** — Board view, grouped by Status
- **High Priority** — Filter: Thesis Score ≥ 4, Status = New 🆕
- **Needs Contact** — Filter: Contact Confidence = ⚠️ Manual
- **Non-CA Founders** — Filter: `Non-CA Founder (Building in Region)` = ✓ AND `Thesis Score` = 2
  > Use this view to review Score 2 leads — non-regional founders explicitly building for CA/DR.
  > These are valid thesis fits but need analyst confirmation before outreach.

---

## 3. Events Database (optional)

Only needed if you want `scout.py` to push startup events to Notion.

### Create the database

1. Create a new full-page database named `Carica Events`
2. Copy its ID → this is your `NOTION_DB_EVENTS`
3. Connect the `Carica Scout` integration (same as above)

### Required properties

| Property name | Type  | Notes |
|--------------|-------|-------|
| `Name`       | Title | |
| `Date`       | Date  | |
| `Location`   | Text  | |
| `URL`        | URL   | |
| `Source`     | Text  | Which calendar URL this came from |
| `Notes`      | Text  | |

---

## 4. Disruption Research Database (optional)

This database receives structured sector memos from the weekly monitor (Step 0b).
Each page represents one disruption theme for a given quarter (e.g. "Fintech — Q1 2026").
Pages are created automatically by `scout.py` if `NOTION_DB_DISRUPTION` is set.

### Create the database

1. Create a new full-page database named `Carica Disruption Research`
2. Copy its ID from the URL → this is your `NOTION_DB_DISRUPTION`
3. Connect the `Carica Scout` integration (same as above)
4. Place it in the same Notion workspace section as `Carica Leads` for easy access

### Required properties

Create exactly these properties (case-sensitive):

| Property name | Type | Notes |
|---|---|---|
| `Name` | Title | Auto-filled: "Sector — Q1 2026" |
| `Sector` | Select | Values auto-created: Fintech, Agritech, Logistics, etc. |
| `Date` | Date | Run date |
| `Refresh Due` | Date | Auto-set to run date + 90 days |
| `Incumbents Disrupted` | Text | Which companies/industries are being threatened |
| `Disruption Pattern` | Select | Values: Bypass · Unbundling · New category · Digitization · Cost collapse · Platform shift |
| `Why Now` | Text | Market timing rationale |
| `Key Evidence` | Text | 3–5 data points |
| `Counterargument` | Text | What could prove this wrong |
| `CA/DR Angle` | Text | Why this matters specifically for the region |
| `Companies Spotted` | Text | Comma-separated company names seen in this theme |
| `Next Research` | Text | Follow-up queries to run next week |
| `Confidence` | Select | Values: **Strong signal** · **Emerging** · **Speculative** |
| `Queries Run` | Text | Tavily queries that produced this memo |
| `Type` | Select | Must have option: **Sector Memo** |

### Recommended views

- **By Sector** — Group by `Sector`; see all themes per vertical at a glance
- **Needs Refresh** — Filter: `Refresh Due` is on or before today; prompts analyst to re-run research
- **Strong Signals** — Filter: `Confidence` = Strong signal; your highest-conviction disruption bets
- **This Quarter** — Filter: `Date` is within the current quarter

### How to use

Each Monday after `scout.py` runs, open this database to see:
1. Which sectors are seeing the most activity this week
2. Which incumbents are under pressure (link to your analyst meetings)
3. The counterargument column — use this as a devil's advocate before a deal
4. `Next Research` — run these queries manually in Tavily or Perplexity for deeper dives

---

## 5. Add keys to `.env`

```bash
cp .env.example .env
```

Then fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
NOTION_API_KEY=secret_...
NOTION_DB_LEADS=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DB_EVENTS=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx       # optional
NOTION_DB_DISRUPTION=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # optional
HUNTER_API_KEY=...                                       # optional
TAVILY_API_KEY=tvly-...                                 # optional, highly recommended
```

---

## 6. Verify setup

```bash
# Enrich one company without pushing — checks API keys and extraction
python enrich.py "Paggo" --no-push

# If that works, push a test lead
python enrich.py "Paggo"

# Confirm it appears in Notion with Status = "New 🆕"
```

If you get a schema mismatch error, the field name in Notion doesn't match
exactly what the writer expects. Check capitalisation and spacing.

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Notion API auth failed` | Check `NOTION_API_KEY` is correct and integration is connected to the database |
| `Notion schema mismatch` | A property name or type doesn't match. The error message will name the field. |
| `Missing required key: NOTION_DB_LEADS` | Copy the database ID from the Notion URL and add to `.env` |
| Lead appears but fields are blank | The property type is wrong (e.g. Text instead of Select). Delete and recreate with correct type. |
