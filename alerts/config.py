"""Alert system configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class AlertSettings:
    """Thresholds, channels, and delivery settings for alerts."""

    # Rule thresholds
    drawdown_levels: Tuple[float, ...] = (-5.0, -10.0, -20.0, -30.0, -40.0, -50.0)
    drawdown_alert_symbols: Tuple[str, ...] = ("VWCE", "CNDX", "SPY", "QQQ")
    portfolio_drop_levels: Tuple[float, ...] = (-5.0, -10.0, -20.0)
    vix_spike_threshold: float = 30.0

    # Reset hysteresis to avoid notification flapping/spam
    drawdown_reset_buffer: float = 5.0
    portfolio_reset_buffer: float = 2.0
    vix_reset_buffer: float = 3.0


    # Retry behavior
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    request_timeout_seconds: float = 10.0

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Email (SMTP)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    smtp_use_tls: bool = True