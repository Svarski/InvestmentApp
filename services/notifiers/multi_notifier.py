"""Channel-aware notifier orchestrator with per-channel isolation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from alerts.config import AlertSettings
from alerts.models import Alert
from services.notifiers.email_notifier import EmailNotifier
from services.notifiers.telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationStats:
    attempted_alerts: int
    sent_alerts: int


class MultiNotifier:
    """Dispatch alerts to configured channels without cross-channel blocking."""

    def __init__(self, settings: AlertSettings) -> None:
        self.settings = settings
        self.telegram = TelegramNotifier(settings)
        self.email = EmailNotifier(settings)
        logger.info("MULTI NOTIFIER ACTIVE channel=%s", self.settings.channel)

    def send_alerts_with_stats(self, alerts: Iterable[Alert]) -> NotificationStats:
        logger.info("MULTI NOTIFIER ACTIVE channel=%s", self.settings.channel)
        if self.settings.channel == "none":
            logger.info("Alert delivery skipped: channel=none")
            return NotificationStats(attempted_alerts=0, sent_alerts=0)

        attempted_count = 0
        sent_count = 0
        for alert in alerts:
            attempted_count += 1
            channel_success = True

            if self.settings.channel == "telegram":
                channel_success = self._send_channel(
                    send_fn=lambda: self.telegram.send_alert(alert),
                    channel_name="telegram",
                    alert_id=alert.id,
                )
            elif self.settings.channel == "email":
                channel_success = self._send_channel(
                    send_fn=lambda: self.email.send_alert(alert),
                    channel_name="email",
                    alert_id=alert.id,
                )
            elif self.settings.channel == "both":
                telegram_ok = self._send_channel(
                    send_fn=lambda: self.telegram.send_alert(alert),
                    channel_name="telegram",
                    alert_id=alert.id,
                )
                email_ok = self._send_channel(
                    send_fn=lambda: self.email.send_alert(alert),
                    channel_name="email",
                    alert_id=alert.id,
                )
                channel_success = telegram_ok and email_ok

            if channel_success:
                sent_count += 1

        return NotificationStats(attempted_alerts=attempted_count, sent_alerts=sent_count)

    def send_plain_email(
        self,
        subject: str,
        body: str,
        recipient: Optional[str],
        html_body: Optional[str] = None,
    ) -> bool:
        logger.info("MULTI NOTIFIER ACTIVE channel=%s", self.settings.channel)
        if self.settings.channel not in {"email", "both"}:
            logger.info("Plain email delivery skipped by channel setting: channel=%s", self.settings.channel)
            return False

        return self._send_channel(
            send_fn=lambda: self.email.send_plain_email(
                subject=subject,
                body=body,
                recipient=recipient,
                html_body=html_body,
            ),
            channel_name="email",
            alert_id="plain_email",
        )

    def send_plain_email_unchecked(
        self,
        subject: str,
        body: str,
        recipient: Optional[str],
        html_body: Optional[str] = None,
    ) -> bool:
        """Send plain email without applying ALERT_CHANNEL gate."""
        logger.info("MULTI NOTIFIER ACTIVE weekly-email path")
        return self._send_channel(
            send_fn=lambda: self.email.send_plain_email(
                subject=subject,
                body=body,
                recipient=recipient,
                html_body=html_body,
            ),
            channel_name="email",
            alert_id="weekly_plain_email",
        )

    def send_telegram_message_unchecked(self, message: str) -> bool:
        """Send Telegram text message without applying ALERT_CHANNEL gate."""
        logger.info("MULTI NOTIFIER ACTIVE telegram-message path")
        return self._send_channel(
            send_fn=lambda: self.telegram.send_message(message),
            channel_name="telegram",
            alert_id="daily_digest_telegram",
        )

    def _send_channel(self, send_fn: Callable[[], None], channel_name: str, alert_id: str) -> bool:
        try:
            self._retry_send(send_fn=send_fn, alert_id=alert_id)
            return True
        except Exception as exc:
            logger.exception("Alert send failed channel=%s id=%s error=%s", channel_name, alert_id, exc)
            return False

    def _retry_send(self, send_fn: Callable[[], None], alert_id: str) -> None:
        attempts = max(1, self.settings.max_retries + 1)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                send_fn()
                return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Alert send attempt failed (%s/%s) id=%s: %s",
                    attempt,
                    attempts,
                    alert_id,
                    exc,
                )
                if attempt < attempts:
                    time.sleep(max(0.0, self.settings.retry_delay_seconds))

        if last_error is not None:
            raise last_error
