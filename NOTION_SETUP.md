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

### Recommended views

- **All Leads** — default table, sorted by Date Found (newest first)
- **Pipeline** — Board view, grouped by Status
- **High Priority** — Filter: Thesis Score ≥ 4, Status = New 🆕
- **Needs Contact** — Filter: Contact Confidence = ⚠️ Manual

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

## 4. Add keys to `.env`

```bash
cp .env.example .env
```

Then fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
NOTION_API_KEY=secret_...
NOTION_DB_LEADS=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DB_EVENTS=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # optional
HUNTER_API_KEY=...                                   # optional
TAVILY_API_KEY=tvly-...                             # optional, highly recommended
```

---

## 5. Verify setup

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
