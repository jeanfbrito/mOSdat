"""H2.1: Multi-channel notifier for scheduled CI smoke results.

Channels:
  slack   — POST minimal blocks payload to a Slack incoming webhook.
  discord — POST minimal embed payload to a Discord webhook.
  email   — Send via stdlib smtplib (SMTP URL: smtp://user:pass@host:port).

CLI usage:
  python -m automation.notify --status fail --label "ubuntu2204 nightly" \
      --report-url https://...

Environment variables (read at notify() call time):
  NOTIFY_CHANNEL  — "slack" | "discord" | "email" (default: "slack")
  NOTIFY_WEBHOOK  — webhook URL (Slack or Discord)
  NOTIFY_EMAIL_SMTP  — SMTP URL (email channel only)
  NOTIFY_EMAIL_TO    — recipient address (email channel only)
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Optional


# ---------------------------------------------------------------------------
# Channel implementations
# ---------------------------------------------------------------------------


def notify_slack(
    webhook_url: str,
    run_label: str,
    status: str,
    report_url: str,
) -> None:
    """POST a minimal Slack blocks payload to *webhook_url*."""
    icon = ":white_check_mark:" if status.lower() == "pass" else ":x:"
    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{icon} *mOSdat smoke* — {run_label}\n"
                        f"Status: *{status.upper()}*\n"
                        f"<{report_url}|View report>"
                    ),
                },
            }
        ]
    }
    _post_json(webhook_url, payload)


def notify_discord(
    webhook_url: str,
    run_label: str,
    status: str,
    report_url: str,
) -> None:
    """POST a minimal Discord embed payload to *webhook_url*."""
    colour = 0x2ECC71 if status.lower() == "pass" else 0xE74C3C  # green / red
    payload = {
        "embeds": [
            {
                "title": f"mOSdat smoke — {run_label}",
                "description": f"Status: **{status.upper()}**\n[View report]({report_url})",
                "color": colour,
            }
        ]
    }
    _post_json(webhook_url, payload)


def notify_email(
    smtp_url: str,
    to: str,
    run_label: str,
    status: str,
    report_url: str,
) -> None:
    """Send a plain-text notification email via *smtp_url*.

    smtp_url format: smtp://user:pass@host:port  (starttls assumed for port 587;
    plain for port 25; ssl for port 465).
    """
    parsed = urllib.parse.urlparse(smtp_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 25
    user: Optional[str] = parsed.username or None
    password: Optional[str] = parsed.password or None

    msg = EmailMessage()
    msg["Subject"] = f"[mOSdat] smoke {status.upper()} — {run_label}"
    msg["From"] = user or "mosdat@ci"
    msg["To"] = to
    msg.set_content(
        f"mOSdat scheduled smoke result\n\n"
        f"Label:  {run_label}\n"
        f"Status: {status.upper()}\n"
        f"Report: {report_url}\n"
    )

    if port == 465:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx) as s:
            if user and password:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            if port == 587:
                s.starttls()
            if user and password:
                s.login(user, password)
            s.send_message(msg)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def notify(
    channel: Optional[str] = None,
    *,
    run_label: str,
    status: str,
    report_url: str,
    webhook_url: Optional[str] = None,
    smtp_url: Optional[str] = None,
    email_to: Optional[str] = None,
) -> None:
    """Dispatch a notification based on *channel* (or NOTIFY_CHANNEL env var).

    Falls back to env vars for webhook_url / smtp_url / email_to when not
    supplied as keyword arguments.
    """
    resolved_channel = (channel or os.environ.get("NOTIFY_CHANNEL", "slack")).lower()
    resolved_webhook = webhook_url or os.environ.get("NOTIFY_WEBHOOK", "")
    resolved_smtp = smtp_url or os.environ.get("NOTIFY_EMAIL_SMTP", "")
    resolved_to = email_to or os.environ.get("NOTIFY_EMAIL_TO", "")

    if resolved_channel == "slack":
        if not resolved_webhook:
            raise ValueError("NOTIFY_WEBHOOK must be set for Slack notifications")
        notify_slack(resolved_webhook, run_label, status, report_url)
    elif resolved_channel == "discord":
        if not resolved_webhook:
            raise ValueError("NOTIFY_WEBHOOK must be set for Discord notifications")
        notify_discord(resolved_webhook, run_label, status, report_url)
    elif resolved_channel == "email":
        if not resolved_smtp:
            raise ValueError("NOTIFY_EMAIL_SMTP must be set for email notifications")
        if not resolved_to:
            raise ValueError("NOTIFY_EMAIL_TO must be set for email notifications")
        notify_email(resolved_smtp, resolved_to, run_label, status, report_url)
    else:
        raise ValueError(f"Unknown NOTIFY_CHANNEL: {resolved_channel!r}. Use slack, discord, or email.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: dict) -> None:  # type: ignore[type-arg]
    """POST *payload* as JSON to *url* using stdlib urllib."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        _ = resp.read()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m automation.notify",
        description="Send a smoke-run notification via Slack, Discord, or email.",
    )
    parser.add_argument("--status", required=True, help="pass or fail")
    parser.add_argument("--label", required=True, dest="run_label", help='Run label, e.g. "ubuntu2204 nightly"')
    parser.add_argument("--report-url", required=True, dest="report_url", help="URL to the CI run or report.html")
    parser.add_argument(
        "--channel",
        default=None,
        help="slack | discord | email (default: NOTIFY_CHANNEL env var, fallback slack)",
    )
    args = parser.parse_args()

    notify(
        channel=args.channel,
        run_label=args.run_label,
        status=args.status,
        report_url=args.report_url,
    )


if __name__ == "__main__":
    _main()
