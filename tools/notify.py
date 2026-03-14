"""
tools/notify.py — Post-run email notifications for Carica Scout.

Sends a plain-text summary email via Gmail SMTP after each weekly run.
Uses stdlib only (smtplib, email.mime) — no extra packages required.

Config keys (all optional, read from .env via config.py):
  NOTIFY_EMAIL_ENABLED   — set to "true" to activate
  NOTIFY_EMAIL_TO        — recipient address
  NOTIFY_EMAIL_FROM      — your Gmail address
  GMAIL_APP_PASSWORD     — Gmail app password (NOT your account password)
                           Generate at: myaccount.google.com/apppasswords
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def send_run_summary(stats: dict, run_date: str, failed: bool = False) -> None:
    """Send a post-run email summary via Gmail SMTP. Silent no-op if disabled or misconfigured."""
    if not config.NOTIFY_EMAIL_ENABLED:
        return

    missing = [k for k, v in {
        "NOTIFY_EMAIL_TO": config.NOTIFY_EMAIL_TO,
        "NOTIFY_EMAIL_FROM": config.NOTIFY_EMAIL_FROM,
        "GMAIL_APP_PASSWORD": config.GMAIL_APP_PASSWORD,
    }.items() if not v]
    if missing:
        logger.warning(f"Email notifications enabled but missing config keys: {', '.join(missing)}")
        return

    attention = failed or stats["candidates"] == 0
    subject = (
        f"⚠️ Carica Scout — Needs Attention ({run_date})"
        if attention else
        f"✅ Carica Scout — Weekly Run Complete ({run_date})"
    )

    lines = [
        f"Run date:           {run_date}",
        f"Mentions found:     {stats['mentions_found']}",
        f"Candidates:         {stats['candidates']}",
        f"Added to Notion:    {stats['added']}",
        f"Skipped (dup):      {stats['skipped_duplicate']}",
        f"Skipped (portf.):   {stats['skipped_portfolio']}",
        f"Failed:             {stats['failed']}",
    ]
    if stats["candidates"] == 0:
        lines.append("")
        lines.append("Zero candidates found — check source URLs and .tmp/batches_cache.json")

    body = "\n".join(lines)

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = config.NOTIFY_EMAIL_FROM
    msg["To"] = config.NOTIFY_EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(config.NOTIFY_EMAIL_FROM, config.GMAIL_APP_PASSWORD)
        server.sendmail(config.NOTIFY_EMAIL_FROM, config.NOTIFY_EMAIL_TO, msg.as_string())

    logger.info(f"  📧 Run summary emailed to {config.NOTIFY_EMAIL_TO}")
