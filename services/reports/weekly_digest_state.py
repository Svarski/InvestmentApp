"""Weekly digest state persistence and state update helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from services.schedulers.weekly_schedule import get_week_key, resolve_local_time

logger = logging.getLogger(__name__)


@dataclass
class WeeklyDigestState:
    """Persistent state for weekly digest aggregation and send dedupe."""

    current_week_key: str = ""
    last_sent_week_key: str = ""
    last_sent_timestamp: Optional[str] = None
    alert_counts: Dict[str, int] = None  # type: ignore[assignment]
    max_drawdown_by_symbol: Dict[str, float] = None  # type: ignore[assignment]
    max_vix: Optional[float] = None
    portfolio_drop_occurred: bool = False
    notable_events: List[str] = None  # type: ignore[assignment]
    last_daily_digest_date: str = ""

    def __post_init__(self) -> None:
        if self.alert_counts is None:
            self.alert_counts = {"market_drawdown": 0, "portfolio_drop": 0, "vix_spike": 0}
        if self.max_drawdown_by_symbol is None:
            self.max_drawdown_by_symbol = {}
        if self.notable_events is None:
            self.notable_events = []

    def to_dict(self) -> Dict[str, object]:
        return {
            "current_week_key": self.current_week_key,
            "last_sent_week_key": self.last_sent_week_key,
            "last_sent_timestamp": self.last_sent_timestamp,
            "alert_counts": self.alert_counts,
            "max_drawdown_by_symbol": self.max_drawdown_by_symbol,
            "max_vix": self.max_vix,
            "portfolio_drop_occurred": self.portfolio_drop_occurred,
            "notable_events": self.notable_events[-20:],
            "last_daily_digest_date": self.last_daily_digest_date,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "WeeklyDigestState":
        return cls(
            current_week_key=str(payload.get("current_week_key", "")),
            last_sent_week_key=str(payload.get("last_sent_week_key", "")),
            last_sent_timestamp=payload.get("last_sent_timestamp") if isinstance(payload.get("last_sent_timestamp"), str) else None,
            alert_counts=payload.get("alert_counts") if isinstance(payload.get("alert_counts"), dict) else None,
            max_drawdown_by_symbol=payload.get("max_drawdown_by_symbol")
            if isinstance(payload.get("max_drawdown_by_symbol"), dict)
            else None,
            max_vix=float(payload["max_vix"]) if payload.get("max_vix") is not None else None,
            portfolio_drop_occurred=bool(payload.get("portfolio_drop_occurred", False)),
            notable_events=payload.get("notable_events") if isinstance(payload.get("notable_events"), list) else None,
            last_daily_digest_date=str(payload.get("last_daily_digest_date", "")),
        )

    @classmethod
    def load_from_file(cls, file_path: str) -> "WeeklyDigestState":
        path = Path(file_path)
        if not path.exists():
            logger.info("Weekly digest state file not found. path=%s", file_path)
            return cls()
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            return cls.from_dict(payload if isinstance(payload, dict) else {})
        except Exception as exc:
            logger.warning("Failed to load weekly digest state. path=%s error=%s", file_path, exc)
            return cls()

    def save_to_file(self, file_path: str) -> bool:
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as file:
                json.dump(self.to_dict(), file, indent=2)
            return True
        except Exception as exc:
            logger.warning("Failed to save weekly digest state. path=%s error=%s", file_path, exc)
            return False


def update_weekly_digest_state(
    state: WeeklyDigestState,
    *,
    market_df: pd.DataFrame,
    alerts: List[object],
    portfolio_drop_pct: Optional[float],
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> WeeklyDigestState:
    """Update rolling weekly aggregates from one worker cycle."""
    now_utc = now_utc or datetime.utcnow()
    now_local = resolve_local_time(now_utc=now_utc, timezone_name=timezone_name)
    week_key = get_week_key(now_local)

    if state.current_week_key != week_key:
        state.current_week_key = week_key
        state.alert_counts = {"market_drawdown": 0, "portfolio_drop": 0, "vix_spike": 0}
        state.max_drawdown_by_symbol = {}
        state.max_vix = None
        state.portfolio_drop_occurred = False
        state.notable_events = []

    for alert in alerts:
        alert_type = getattr(alert, "type", "other")
        if alert_type in state.alert_counts:
            state.alert_counts[alert_type] += 1
        if len(state.notable_events) < 20:
            state.notable_events.append(getattr(alert, "message", "Alert event"))

    if market_df is not None and not market_df.empty:
        for _, row in market_df.iterrows():
            symbol = str(row.get("Symbol", "")).upper()
            drawdown = _safe_float(row.get("Drawdown from ATH %"))
            price = _safe_float(row.get("Price"))
            if symbol and drawdown is not None:
                existing = state.max_drawdown_by_symbol.get(symbol)
                if existing is None or drawdown < existing:
                    state.max_drawdown_by_symbol[symbol] = drawdown
            if symbol == "VIX" and price is not None:
                if state.max_vix is None or price > state.max_vix:
                    state.max_vix = price

    if portfolio_drop_pct is not None and portfolio_drop_pct <= -5:
        state.portfolio_drop_occurred = True

    logger.info(
        "Weekly digest state updated week=%s counts=%s max_vix=%s",
        state.current_week_key,
        state.alert_counts,
        state.max_vix,
    )
    return state


def should_send_daily_digest(
    state: WeeklyDigestState,
    *,
    enabled: bool,
    hour: int,
    timezone_name: str,
    now_utc: Optional[datetime] = None,
) -> bool:
    """Return True when daily digest is due and not already sent today."""
    if not enabled:
        return False
    now_local = resolve_local_time(now_utc=now_utc, timezone_name=timezone_name)
    if now_local.hour < hour:
        return False
    today_key = now_local.date().isoformat()
    return state.last_daily_digest_date != today_key


def mark_daily_digest_sent(state: WeeklyDigestState, timezone_name: str, now_utc: Optional[datetime] = None) -> None:
    """Mark daily digest as sent for current local date."""
    now_local = resolve_local_time(now_utc=now_utc, timezone_name=timezone_name)
    state.last_daily_digest_date = now_local.date().isoformat()


def _safe_float(value: object) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
