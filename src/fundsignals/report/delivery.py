"""Optional delivery of the weekly report by email (SMTP) and Slack (incoming webhook).

Each channel is skipped when its env keys are missing; failures are logged and never raised.
"""

from __future__ import annotations

import logging
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx

from fundsignals.models import FundScore
from fundsignals.report.writer import why_line

log = logging.getLogger(__name__)

SLACK_TOP_N = 5
DEFAULT_SMTP_PORT = 587
TIMEOUT_SECONDS = 30


def _week_label(paths: dict[str, Path], scores: list[FundScore]) -> str:
    if scores:
        return scores[0].week_of.isoformat()
    return Path(paths["md"]).parent.name if "md" in paths else "unknown week"


def _send_email(
    paths: dict[str, Path], scores: list[FundScore], env: dict, smtp_factory
) -> str:
    host, recipients_raw = env.get("SMTP_HOST"), env.get("REPORT_RECIPIENTS")
    if not host or not recipients_raw:
        log.info("email delivery skipped: SMTP_HOST or REPORT_RECIPIENTS not set")
        return "skipped"
    recipients = [r.strip() for r in re.split(r"[,;]", recipients_raw) if r.strip()]
    if not recipients:
        log.info("email delivery skipped: REPORT_RECIPIENTS is empty")
        return "skipped"
    user, password = env.get("SMTP_USER"), env.get("SMTP_PASSWORD")
    try:
        port = int(env.get("SMTP_PORT") or DEFAULT_SMTP_PORT)
        md_path = Path(paths["md"])
        markdown = md_path.read_text(encoding="utf-8")

        msg = EmailMessage()
        msg["Subject"] = f"Avronna Fund Signals: week of {_week_label(paths, scores)}"
        msg["From"] = user or "fundsignals@localhost"
        msg["To"] = ", ".join(recipients)
        msg.set_content(markdown)
        if "html" in paths and Path(paths["html"]).exists():
            msg.add_alternative(Path(paths["html"]).read_text(encoding="utf-8"), subtype="html")
        msg.add_attachment(
            markdown.encode("utf-8"), maintype="text", subtype="markdown", filename=md_path.name
        )
        if "csv" in paths and Path(paths["csv"]).exists():
            msg.add_attachment(
                Path(paths["csv"]).read_bytes(), maintype="text", subtype="csv",
                filename=Path(paths["csv"]).name,
            )

        smtp = smtp_factory(host, port, timeout=TIMEOUT_SECONDS)
        try:
            if user and password:
                smtp.starttls()
                smtp.login(user, password)
            smtp.send_message(msg, to_addrs=recipients)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 - closing a broken connection is best effort
                pass
    except Exception as exc:  # noqa: BLE001 - delivery must never crash the run
        log.warning("email delivery failed: %s", type(exc).__name__)
        return "failed"
    log.info("email delivered to %d recipient(s)", len(recipients))
    return "sent"


def slack_summary(scores: list[FundScore], week_label: str) -> str:
    """Short top-5 summary text for Slack."""
    lines = [f"*Avronna Fund Signals: week of {week_label}*"]
    top = sorted(scores, key=lambda f: (f.rank, f.fund_id))[:SLACK_TOP_N]
    if not top:
        lines.append("No funds showed compute-bottleneck signals this week.")
    for fs in top:
        line = f"{fs.rank}. {fs.fund_name} (score {fs.score:.2f}): {why_line(fs.top_signals)}"
        if fs.top_signals:
            line += f" <{fs.top_signals[0].url}|source>"
        lines.append(line)
    return "\n".join(lines)


def _send_slack(
    paths: dict[str, Path], scores: list[FundScore], env: dict, http_client
) -> str:
    webhook = env.get("SLACK_WEBHOOK_URL")
    if not webhook:
        log.info("slack delivery skipped: SLACK_WEBHOOK_URL not set")
        return "skipped"
    client = http_client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        text = slack_summary(scores, _week_label(paths, scores))
        resp = client.post(webhook, json={"text": text})
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - delivery must never crash the run
        # Never log the webhook URL: it is a secret.
        log.warning("slack delivery failed: %s", type(exc).__name__)
        return "failed"
    finally:
        if http_client is None:
            client.close()
    log.info("slack summary posted")
    return "sent"


def deliver(
    paths: dict[str, Path],
    scores: list[FundScore],
    env: dict,
    smtp_factory=smtplib.SMTP,
    http_client=None,
) -> dict[str, str]:
    """Send the report by email and Slack where configured. Returns status per channel."""
    return {
        "email": _send_email(paths, scores, env, smtp_factory),
        "slack": _send_slack(paths, scores, env, http_client),
    }
