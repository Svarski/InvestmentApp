"""Telegram delivery helper for alert notifications."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from alerts.config import AlertSettings
from alerts.models import Alert

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send alerts to Telegram Bot API."""

    def __init__(self, settings: AlertSettings) -> None:
        self.settings = settings

    def send_alert(self, alert: Alert) -> None:
        text = f"[{alert.severity.upper()}] {alert.message}\n({alert.timestamp})"
        self.send_message(text)
        logger.info("Alert sent to telegram id=%s", alert.id)

    def send_message(self, text: str) -> None:
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            raise ValueError("Telegram settings missing: bot token or chat id.")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(url=url, data=payload, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Telegram request failed: {exc}") from exc

        parsed = json.loads(body)
        if not parsed.get("ok"):
            raise RuntimeError(f"Telegram API rejected message: {body}")
