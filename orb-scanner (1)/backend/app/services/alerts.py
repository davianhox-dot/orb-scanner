"""
Alert dispatch.

Each channel is a small, independent function so you can call one, several,
or all of them for a given event. `notify_all` fires whichever channels are
configured (empty credentials = silently skipped, not an error) and logs
failures without letting one broken channel block the others.

Browser notifications aren't sent from here — they're pushed to connected
clients over the (future) WebSocket/SSE endpoint, since they require an
active browser session rather than a webhook.
"""
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from app.core.config import get_settings
from app.models.models import ScanResult

logger = logging.getLogger(__name__)


def _format_alert_text(result: ScanResult, reason: str) -> str:
    return (
        f"🚨 {reason}\n"
        f"{result.ticker} — {result.company}\n"
        f"Price: ${result.price:.2f} | Gap: {result.gap_pct:.1f}% | "
        f"RelVol: {result.relative_volume:.1f}x | Score: {result.score:.0f}\n"
        f"Catalyst: {', '.join(result.catalyst_tags) or 'None'}"
    )


async def send_discord(message: str) -> bool:
    settings = get_settings()
    if not settings.DISCORD_WEBHOOK_URL:
        return False
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(settings.DISCORD_WEBHOOK_URL, json={"content": message})
        return resp.status_code in (200, 204)


async def send_telegram(message: str) -> bool:
    settings = get_settings()
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        return False
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": message})
        return resp.status_code == 200


def send_email(subject: str, message: str) -> bool:
    settings = get_settings()
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


async def notify_all(result: ScanResult, reason: str) -> dict[str, bool]:
    """Fire every configured channel for one alert-worthy scan result."""
    text = _format_alert_text(result, reason)
    outcomes = {}
    try:
        outcomes["discord"] = await send_discord(text)
    except httpx.HTTPError:
        logger.exception("Discord alert failed")
        outcomes["discord"] = False
    try:
        outcomes["telegram"] = await send_telegram(text)
    except httpx.HTTPError:
        logger.exception("Telegram alert failed")
        outcomes["telegram"] = False
    outcomes["email"] = send_email(f"ORB Scanner alert: {result.ticker}", text)
    return outcomes
