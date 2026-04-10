"""Weekly scheduling helpers separated from report content generation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

WEEKDAY_TO_INT = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def get_week_key(now_local: datetime) -> str:
    year, week, _ = now_local.isocalendar()
    return f"{year}-W{week:02d}"


def resolve_local_time(now_utc: Optional[datetime], timezone_name: str) -> datetime:
    now_utc = now_utc or datetime.utcnow()
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        logger.warning("Invalid weekly digest timezone=%s, falling back to UTC", timezone_name)
        tz = ZoneInfo("UTC")
    return now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)


def is_weekly_schedule_due(
    *,
    last_sent_week_key: str,
    enabled: bool,
    day: str,
    hour: int,
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> bool:
    if not enabled:
        return False

    now_local = resolve_local_time(now_utc=now_utc, timezone_name=timezone_name)
    current_week_key = get_week_key(now_local)
    if last_sent_week_key == current_week_key:
        return False

    weekday_target = WEEKDAY_TO_INT.get(day.lower(), 0)
    week_start = now_local - timedelta(days=now_local.weekday())
    scheduled_dt = week_start.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=weekday_target)
    return now_local >= scheduled_dt
