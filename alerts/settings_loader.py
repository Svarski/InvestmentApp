"""Shared environment-driven loader for AlertSettings."""

from __future__ import annotations

import os
from typing import Optional, Tuple

from dotenv import load_dotenv

from alerts.config import AlertSettings

# Load .env without overriding explicitly set OS env vars.
load_dotenv(override=False)


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int, min_value: Optional[int] = None) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    if min_value is not None:
        parsed = max(min_value, parsed)
    return parsed


def _parse_float(value: Optional[str], default: float, min_value: Optional[float] = None) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    if min_value is not None:
        parsed = max(min_value, parsed)
    return parsed


def _parse_float_tuple(value: Optional[str], default: Tuple[float, ...]) -> Tuple[float, ...]:
    if value is None or value.strip() == "":
        return default
    parts = [part.strip() for part in value.split(",") if part.strip()]
    parsed: list[float] = []
    for part in parts:
        try:
            parsed.append(float(part))
        except ValueError:
            continue
    return tuple(parsed) if parsed else default


def _parse_symbol_tuple(value: Optional[str], default: Tuple[str, ...]) -> Tuple[str, ...]:
    if value is None or value.strip() == "":
        return default
    parsed = tuple(symbol.strip().upper() for symbol in value.split(",") if symbol.strip())
    return parsed if parsed else default


def get_alert_settings() -> AlertSettings:
    """Return unified AlertSettings from environment variables with safe fallbacks."""
    defaults = AlertSettings()

    channel = os.getenv("ALERT_CHANNEL", "none").strip().lower()
    channel = channel if channel in {"none", "telegram", "email", "both"} else "none"

    return AlertSettings(
        # Alert channel settings
        channel=channel,
        # Rule thresholds/settings (env override supported)
        drawdown_levels=_parse_float_tuple(os.getenv("ALERT_DRAWDOWN_LEVELS"), defaults.drawdown_levels),
        drawdown_alert_symbols=_parse_symbol_tuple(
            os.getenv("ALERT_DRAWDOWN_SYMBOLS"), defaults.drawdown_alert_symbols
        ),
        portfolio_drop_levels=_parse_float_tuple(
            os.getenv("ALERT_PORTFOLIO_DROP_LEVELS"), defaults.portfolio_drop_levels
        ),
        vix_spike_threshold=_parse_float(os.getenv("ALERT_VIX_SPIKE_THRESHOLD"), defaults.vix_spike_threshold),
        drawdown_reset_buffer=_parse_float(
            os.getenv("ALERT_DRAWDOWN_RESET_BUFFER"), defaults.drawdown_reset_buffer, min_value=0.0
        ),
        portfolio_reset_buffer=_parse_float(
            os.getenv("ALERT_PORTFOLIO_RESET_BUFFER"), defaults.portfolio_reset_buffer, min_value=0.0
        ),
        vix_reset_buffer=_parse_float(os.getenv("ALERT_VIX_RESET_BUFFER"), defaults.vix_reset_buffer, min_value=0.0),
        # Retry and timeout settings
        max_retries=_parse_int(os.getenv("ALERT_MAX_RETRIES"), defaults.max_retries, min_value=0),
        retry_delay_seconds=_parse_float(
            os.getenv("ALERT_RETRY_DELAY_SECONDS"), defaults.retry_delay_seconds, min_value=0.0
        ),
        request_timeout_seconds=_parse_float(
            os.getenv("ALERT_REQUEST_TIMEOUT_SECONDS"), defaults.request_timeout_seconds, min_value=1.0
        ),
        # Telegram settings
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        # Email SMTP settings
        smtp_host=os.getenv("SMTP_HOST"),
        smtp_port=_parse_int(os.getenv("SMTP_PORT"), defaults.smtp_port, min_value=1),
        smtp_username=os.getenv("SMTP_USERNAME"),
        smtp_password=os.getenv("SMTP_PASSWORD"),
        email_from=os.getenv("EMAIL_FROM"),
        email_to=os.getenv("EMAIL_TO"),
        smtp_use_tls=_parse_bool(os.getenv("SMTP_USE_TLS"), default=defaults.smtp_use_tls),
    )


def load_alert_settings_from_env() -> AlertSettings:
    """Backward-compatible alias for existing imports."""
    return get_alert_settings()
