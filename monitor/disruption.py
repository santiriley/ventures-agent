"""
monitor/disruption.py — Disruption intelligence layer for Carica Scout.

Runs as Step 0 of the weekly monitor (scout.py). Uses 2-3 Tavily basic searches
to gather current trend context, then calls Claude Sonnet to synthesize a short
market memo and generate dynamic Tavily search queries for this week's run.

Primary function:
  research_disruption_trends(dry_run: bool = False) -> dict
    Returns: {
      "queries": list[str],   # dynamic queries to pass to scan_tavily_queries()
      "memo_path": str,       # path to saved .md file (empty on dry_run or failure)
      "memo_text": str,       # synthesized memo (empty on failure)
    }

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
    "fintech payments startup Latin America disruption 2025 2026 emerging",
    "B2B SaaS startup Central America Dominican Republic new company 2025 2026",
    "venture capital investment trends Central America Dominican Republic 2025 2026",
]

_MEMO_PROMPT_TEMPLATE = """\
You are a VC analyst assistant for Carica VC, a Central American and Dominican Republic fund.

The fund's portfolio is concentrated in these sectors: {sectors}.
Key problem domains the fund has invested in: {domains}.

Based on the Tavily search results below, write a concise market intelligence memo \
(200-300 words) covering:
1. Notable emerging trends in the CA/DR startup ecosystem
2. Sectors gaining momentum in the region
3. Any global tech trends that have NOT yet been replicated in CA/DR and represent \
an opportunity for a local founder

For each major trend you identify, suggest 1-2 specific Tavily search queries (short, \
semantic — no operators) that would surface early-stage startups in CA/DR building \
in that space.

Return ONLY valid JSON matching this schema:
{{
  "trends": [
    {{
      "industry": "payments",
      "trend": "embedded finance APIs for SME platforms",
      "search_queries": [
        "embedded finance API startup Central America 2025 2026",
        "banking as a service BaaS LATAM startup seed"
      ]
    }}
  ],
  "memo_summary": "2-3 paragraph executive summary of what's changing and what to watch for"
}}

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

    prompt = _MEMO_PROMPT_TEMPLATE.format(
        sectors=sectors,
        domains=domains,
        results="\n\n".join(combined_results)[:6000],
    )

    memo_text = ""
    dynamic_queries: list[str] = []

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

        # Extract dynamic queries from each trend (capped at 8 total)
        for trend in data.get("trends", []):
            for q in trend.get("search_queries", []):
                if q and len(dynamic_queries) < 8:
                    dynamic_queries.append(q)

    except json.JSONDecodeError as exc:
        logger.warning(f"Step 0 — Disruption memo JSON parse failed: {exc}. Skipping dynamic queries.")
    except Exception as exc:
        logger.warning(f"Step 0 — Disruption Claude call failed: {exc}. Skipping memo.")
        return {"queries": [], "memo_path": "", "memo_text": ""}

    if not memo_text:
        logger.info("Step 0 — Empty memo; skipping save.")
        return {"queries": dynamic_queries, "memo_path": "", "memo_text": ""}

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
    return {"queries": dynamic_queries, "memo_path": memo_path, "memo_text": memo_text}
