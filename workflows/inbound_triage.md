# Workflow C — Inbound Triage
**Last updated:** 2026-03-10

## Objective
Process unstructured inbound content (forwarded emails, WhatsApp messages, pitch decks pasted as text, LinkedIn bios, etc.) and produce an enriched Notion profile.

## Trigger
```bash
python enrich.py --inbound
```

## Required inputs
- Any raw text the analyst pastes in response to the prompt
- Terminate input by typing `done` on a new line

## Sequence

1. **Accept multi-line paste**
   - CLI prompts analyst to paste content
   - Collect lines until `done` is entered
   - Pass full text as `raw_input` to `enrich_with_claude()`

2. **Continue as Workflow A (On-Demand Enrichment)**
   - `enrich_with_claude(raw_input)` extracts structured profile
   - `geo_score()` scores each founder
   - `thesis_score()` scores against thesis
   - `find_contact()` finds best email
   - Portfolio check
   - `push_lead()` → Notion
   - Print profile to terminal

## Expected output
- Enriched profile in Notion (Status = "New 🆕")
- Terminal summary

## Edge cases

| Situation | Handling |
|---|---|
| Paste is in Spanish | Claude handles natively, no translation needed |
| Content describes multiple companies | Claude extracts the primary one; add note for analyst |
| No company name identifiable | Set name = "Unknown – {first 40 chars of input}", flag in notes |
| Empty paste | Exit with message "No input received" |
| Content is a URL | Works — Claude will use URL as context |

## Notes
- Best for: forwarded pitch emails, WhatsApp founder intros, copied LinkedIn bios, Notion form submissions
- Analyst can paste content in any language — Spanish is fully supported
- Informal names or slang may reduce extraction accuracy — Claude will flag uncertainty in notes
