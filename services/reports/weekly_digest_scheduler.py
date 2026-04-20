"""Weekly digest schedule wrappers and send markers."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from services.schedulers.weekly_schedule import get_week_key, is_weekly_schedule_due, resolve_local_time

from .weekly_digest_state import WeeklyDigestState


def should_send_weekly_digest(
    state: WeeklyDigestState,
    *,
    enabled: bool,
    day: str,
    hour: int,
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> bool:
    """Backward-compatible wrapper around scheduler module."""
    return is_weekly_schedule_due(
        last_sent_week_key=state.last_sent_week_key,
        enabled=enabled,
        day=day,
        hour=hour,
        timezone_name=timezone_name,
        now_utc=now_utc,
    )


def mark_weekly_digest_sent(state: WeeklyDigestState, timezone_name: str, now_utc: Optional[datetime] = None) -> None:
    """Mark digest as sent for current local week."""
    now_utc = now_utc or datetime.utcnow()
    now_local = resolve_local_time(now_utc=now_utc, timezone_name=timezone_name)
    state.last_sent_week_key = get_week_key(now_local)
    state.last_sent_timestamp = now_local.isoformat()
