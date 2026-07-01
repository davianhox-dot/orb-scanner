"""
Alert dispatch for the cloud path — synchronous senders, wired directly into
run_scan.py so any result scoring at or above SCORE_ALERT_THRESHOLD fires
automatically on every scheduled scan. Each channel is skipped silently if
its credentials aren't set, so you can enable just the ones you want.
"""
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from cloud.config import Settings
from cloud.db import ScanResult

logger = logging.getLogger(__name__)


def _format_alert_text(result: ScanResult) -> str:
    return (
        f"New high-score candidate: {result.ticker} — {result.company}\n"
        f"Price: ${result.price:.2f} | Gap: {result.gap_pct:.1f}% | "
        f"RelVol: {result.relative_volume:.1f}x | Score: {result.score:.0f}\n"
        f"Catalyst: {', '.join(result.catalyst_tags) or 'None'}"
    )


def send_discord(settings: Settings, message: str) -> bool:
    if not settings.DISCORD_WEBHOOK_URL:
        return False
    try:
        resp = httpx.post(settings.DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10.0)
        return resp.status_code in (200, 204)
    except httpx.HTTPError:
        logger.exception("Discord alert failed")
        return False


def send_telegram(settings: Settings, message: str) -> bool:
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        return False
    try:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = httpx.post(url, json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": message}, timeout=10.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        logger.exception("Telegram alert failed")
        return False


def send_email(settings: Settings, subject: str, message: str) -> bool:
    if not (settings.SMTP_HOST and settings.ALERT_EMAIL_TO):
        return False
    try:
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_USER or "orb-scanner@localhost"
        msg["To"] = settings.ALERT_EMAIL_TO
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        logger.exception("Email alert failed")
        return False


def notify_high_score(settings: Settings, result: ScanResult) -> dict[str, bool]:
    """Fires every configured channel for one result that crossed
    SCORE_ALERT_THRESHOLD. Called from run_scan.py after scoring."""
    text = _format_alert_text(result)
    outcomes = {
        "discord": send_discord(settings, text),
        "telegram": send_telegram(settings, text),
        "email": send_email(settings, f"ORB Scanner alert: {result.ticker}", text),
    }
    fired = [k for k, v in outcomes.items() if v]
    if fired:
        logger.info("Alert sent for %s via: %s", result.ticker, ", ".join(fired))
    return outcomes
