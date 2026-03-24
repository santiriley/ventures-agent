"""
monitor/disruption.py — Disruption intelligence layer for Carica Scout.

Runs as Step 0 of the weekly monitor (scout.py). Uses Tavily basic searches
to gather current trend context, then calls Claude Sonnet to synthesize structured
sector memos and generate dynamic Tavily search queries for this week's run.

Primary function:
  research_disruption_trends(dry_run: bool = False) -> dict
    Returns: {
      "queries":   list[str],   # dynamic queries to pass to scan_tavily_queries()
      "memo_path": str,         # path to saved .md file (empty on dry_run or failure)
      "memo_text": str,         # synthesized memo summary (empty on failure)
      "themes":    list[dict],  # structured sector memos for push_disruption_memo()
    }

Each theme dict has:
  sector, incumbents_disrupted, disruption_pattern, why_now,
  key_evidence (list), counterargument, ca_dr_angle,
  companies_spotted (list), next_research (list),
  confidence (strong_signal|emerging|speculative), search_queries (list)

All failure modes fail gracefully — this step must NEVER block the weekly run.
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from pathlib import Path

import anthropic
import requests

import config

logger = logging.getLogger(__name__)

# Tavily queries used to gather current context before the Claude synthesis call.
# Keep these broad — they provide real-world grounding for the LLM prompt.
_CONTEXT_QUERIES: list[str] = [
    "fintech payments wealth management startup Latin America 2025 2026",
    "B2B SaaS enterprise AI startup Central America Dominican Republic 2025 2026",
    "venture capital investment trends Central America Dominican Republic 2025 2026",
    "mobility transport logistics tech startup Latin America 2025 2026",
    "climate tech carbon credits sustainability startup Central America 2025 2026",
]

_MEMO_PROMPT_TEMPLATE = """\
You are a VC analyst assistant for Carica VC, a Central American and Dominican Republic fund.

Fund thesis: invests ≥$100K USD in tech startups with at least one CA/DR founder \
(or a non-regional founder explicitly building for the CA/DR market), an MVP, and \
early traction. Target stages: pre-seed, seed, Series A.

Portfolio sectors: {sectors}. Key problem domains: {domains}.

Current forward-looking investment hypotheses:
{hypotheses}

Based on the search results below, identify 3-5 significant disruption themes relevant \
to CA/DR. For each theme, think about:
- What incumbent industry/player is being disrupted?
- Why is disruption happening NOW (infrastructure, regulation, behavior shift)?
- What is the disruption pattern (bypass incumbent, unbundling, new category, \
digitization, cost collapse)?
- What is the specific CA/DR angle — why does this matter in the region?
- What companies are already doing this (if you see names in the results)?
- What would prove this thesis wrong?

Return ONLY valid JSON matching this schema exactly:
{{
  "themes": [
    {{
      "sector": "Fintech",
      "incumbents_disrupted": "Banco Nacional, BAC, Tigo Money",
      "disruption_pattern": "Bypass",
      "why_now": "70% unbanked population + smartphone penetration crossing 60% in CA",
      "key_evidence": [
        "3 new embedded finance startups announced in CR this quarter",
        "BAC launched digital wallet — confirms market validation"
      ],
      "counterargument": "Incumbent banks have launched their own apps; regulatory moat remains high",
      "ca_dr_angle": "SME credit gap is $4B in CA — banks don't serve businesses under $500K revenue",
      "companies_spotted": ["Paggo", "Zunify"],
      "next_research": [
        "embedded finance API startup Central America seed 2025 2026",
        "SME credit fintech Dominican Republic founder"
      ],
      "confidence": "strong_signal"
    }}
  ],
  "memo_summary": "2-3 paragraph executive summary of what is changing and what to watch for this week"
}}

confidence must be one of: strong_signal, emerging, speculative
disruption_pattern must be one of: Bypass, Unbundling, New category, Digitization, Cost collapse, Platform shift

Search results:
{results}
"""


def research_disruption_trends(dry_run: bool = False) -> dict:
    """
    Run disruption trend research via Tavily + Claude Sonnet.

    Returns dict with:
      queries  — list[str]: dynamic queries for scan_tavily_queries() extra_queries param
      memo_path — str: absolute path to saved markdown file (empty if not saved)
      memo_text — str: synthesized memo text (empty on failure)

    All failures are logged as warnings and return an empty result dict so the
    weekly run is never blocked.
    """
    tavily_key = config.get_optional_key("TAVILY_API_KEY")
    if not tavily_key:
        logger.info("Step 0 — No TAVILY_API_KEY; skipping disruption trend research.")
        return {"queries": [], "memo_path": "", "memo_text": ""}

    # ── Step 1: Gather trend context via Tavily basic searches ─────────────
    combined_results: list[str] = []
    for query in _CONTEXT_QUERIES:
        try:
            payload = {
                "api_key": tavily_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": False,
                "include_raw_content": False,
            }
            resp = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=config.REQUEST_TIMEOUT,
            )
            if resp.status_code == 401:
                logger.warning("Step 0 — Tavily auth failed; stopping context search.")
                break
            if resp.status_code == 429:
                logger.warning("Step 0 — Tavily rate limit; stopping context search.")
                break
            resp.raise_for_status()
            results = resp.json().get("results", [])
            snippet = " ".join(
                f"[{r.get('title', '')}] {r.get('content', '')}"
                for r in results
            )
            if snippet.strip():
                combined_results.append(snippet)
        except Exception as exc:
            logger.warning(f"Step 0 — Disruption context query failed: {exc}")
        time.sleep(config.REQUEST_DELAY)

    if not combined_results:
        logger.info("Step 0 — No Tavily results; skipping disruption memo.")
        return {"queries": [], "memo_path": "", "memo_text": ""}

    # ── Step 2: Synthesize with Claude Sonnet ──────────────────────────────
    try:
        from portfolio.patterns import PORTFOLIO_PATTERNS

        sectors = ", ".join(PORTFOLIO_PATTERNS["top_sectors"])
        domains = ", ".join(PORTFOLIO_PATTERNS["top_domains"])
    except Exception:
        sectors = "fintech, saas, logistics"
        domains = "payments, commerce, logistics"

    # Read thesis hypotheses (optional — fail gracefully if file missing or unreadable)
    hypotheses_text = ""
    try:
        hypotheses_path = config.ROOT / "thesis_hypotheses.md"
        if hypotheses_path.exists():
            raw_hyp = hypotheses_path.read_text(encoding="utf-8")
            # Strip header comments; keep only the hypothesis sections
            marker = "## Hypothesis"
            idx = raw_hyp.find(marker)
            hypotheses_text = raw_hyp[idx:] if idx >= 0 else raw_hyp
            logger.info(f"Step 0 — Loaded thesis hypotheses ({len(hypotheses_text)} chars)")
    except Exception as exc:
        logger.warning(f"Step 0 — Could not read thesis_hypotheses.md: {exc}")

    prompt = _MEMO_PROMPT_TEMPLATE.format(
        sectors=sectors,
        domains=domains,
        hypotheses=hypotheses_text or "No specific hypotheses provided — use portfolio sectors as the primary lens.",
        results="\n\n".join(combined_results)[:6000],
    )

    memo_text = ""
    dynamic_queries: list[str] = []
    themes: list[dict] = []

    try:
        client = anthropic.Anthropic(api_key=config.get_key("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model=config.CLAUDE_MODEL_RESEARCH,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        logger.info(
            f"Step 0 — Disruption memo: "
            f"{message.usage.input_tokens}in / {message.usage.output_tokens}out tokens"
        )

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0]

        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            raw = raw[brace_start : brace_end + 1]

        data = json.loads(raw)
        memo_text = data.get("memo_summary", "").strip()
        themes: list[dict] = data.get("themes", [])

        # Extract dynamic queries from each theme's search_queries (capped at 8 total)
        for theme in themes:
            for q in theme.get("next_research", []):
                if q and len(dynamic_queries) < 8:
                    dynamic_queries.append(q)

        logger.info(f"Step 0 — {len(themes)} disruption theme(s) extracted.")

    except json.JSONDecodeError as exc:
        logger.warning(f"Step 0 — Disruption memo JSON parse failed: {exc}. Skipping dynamic queries.")
        themes = []
    except Exception as exc:
        logger.warning(f"Step 0 — Disruption Claude call failed: {exc}. Skipping memo.")
        return {"queries": [], "memo_path": "", "memo_text": "", "themes": []}

    if not memo_text:
        logger.info("Step 0 — Empty memo; skipping save.")
        return {"queries": dynamic_queries, "memo_path": "", "memo_text": "", "themes": themes}

    # ── Step 3: Save memo to .tmp/ ─────────────────────────────────────────
    run_date = datetime.date.today().isoformat()
    memo_filename = f"disruption_memo_{run_date}.md"
    memo_path = str(config.TMP_DIR / memo_filename)

    if not dry_run:
        try:
            Path(memo_path).write_text(
                f"# Disruption Intelligence Memo — {run_date}\n\n{memo_text}\n",
                encoding="utf-8",
            )
            logger.info(f"Step 0 — Disruption memo saved: {memo_path}")
        except Exception as exc:
            logger.warning(f"Step 0 — Failed to save disruption memo: {exc}")
            memo_path = ""
    else:
        logger.info(f"Step 0 — [dry-run] Disruption memo would be saved to: {memo_path}")
        memo_path = ""  # don't report a path that wasn't written

    logger.info(f"Step 0 — {len(dynamic_queries)} dynamic disruption queries generated.")
    return {"queries": dynamic_queries, "memo_path": memo_path, "memo_text": memo_text, "themes": themes}
