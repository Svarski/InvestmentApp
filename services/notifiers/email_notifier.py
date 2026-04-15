"""SMTP email delivery helper for alert and digest notifications."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from alerts.config import AlertSettings
from alerts.models import Alert

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Send SMTP notifications with optional HTML content."""

    def __init__(self, settings: AlertSettings) -> None:
        self.settings = settings

    def send_alert(self, alert: Alert) -> None:
        subject = f"[{alert.severity.upper()}] {alert.type}"
        body = f"{alert.message}\n\nTimestamp: {alert.timestamp}\nAlert ID: {alert.id}"
        self.send_plain_email(subject=subject, body=body, recipient=self.settings.email_to)
        logger.info("Alert sent to email id=%s", alert.id)

    def send_plain_email(
        self,
        subject: str,
        body: str,
        recipient: Optional[str],
        html_body: Optional[str] = None,
    ) -> None:
        required = [
            self.settings.smtp_host,
            self.settings.smtp_username,
            self.settings.smtp_password,
            self.settings.email_from,
            recipient or self.settings.email_to,
        ]
        if any(not item for item in required):
            raise ValueError("Email settings missing: SMTP host/credentials/from/to.")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.email_from
        message["To"] = recipient or self.settings.email_to
        message.set_content(body)
        if html_body and html_body.strip():
            message.add_alternative(html_body, subtype="html")

        if self.settings.smtp_use_tls:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(message)
            logger.info("Plain email sent successfully recipient=%s", message["To"])
            return

        with smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as smtp:
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)
        logger.info("Plain email sent successfully recipient=%s", message["To"])
