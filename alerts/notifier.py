"""Alert delivery adapters for Telegram and email."""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from alerts.config import AlertSettings
from alerts.models import Alert
from services.notifiers.multi_notifier import MultiNotifier, NotificationStats

logger = logging.getLogger(__name__)


class AlertNotifier:
    """Deliver alerts to configured channels with simple retries."""

    def __init__(self, settings: AlertSettings) -> None:
        self.settings = settings
        self._multi_notifier = MultiNotifier(settings)
        logger.info("Notifier initialized. channel=%s", self.settings.channel)
        if self.settings.channel in {"email", "both"}:
            if self.settings.email_to:
                logger.info("Email notifier enabled. recipient=%s", self.settings.email_to)
            else:
                logger.warning("Email notifier enabled but EMAIL_TO is missing.")
        else:
            logger.info("Email notifier disabled for current channel configuration.")
        if self.settings.channel in {"telegram", "both"}:
            logger.info("Telegram notifier enabled.")
        else:
            logger.info("Telegram notifier disabled for current channel configuration.")

    def send_alerts(self, alerts: Iterable[Alert]) -> int:
        """Send alerts to active channels; never raises to caller. Returns count of alerts processed."""
        stats = self.send_alerts_with_stats(alerts)
        return stats.sent_alerts

    def send_alerts_with_stats(self, alerts: Iterable[Alert]) -> NotificationStats:
        """Send alerts and return attempted/success counters."""
        return self._multi_notifier.send_alerts_with_stats(alerts)

    def send_plain_email(self, subject: str, body: str, recipient: Optional[str] = None) -> bool:
        """Send plain text email with retries; return True on success."""
        return self._multi_notifier.send_plain_email(subject=subject, body=body, recipient=recipient)

    def send_weekly_summary_email(
        self,
        subject: str,
        body: str,
        recipient: Optional[str] = None,
        html_body: Optional[str] = None,
    ) -> bool:
        """Send weekly summary email independent of ALERT_CHANNEL."""
        return self._multi_notifier.send_plain_email_unchecked(
            subject=subject,
            body=body,
            recipient=recipient,
            html_body=html_body,
        )

    def send_telegram(self, message: str) -> bool:
        """Send Telegram message independent of ALERT_CHANNEL."""
        return self._multi_notifier.send_telegram_message_unchecked(message)
