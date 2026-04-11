"""Weekly digest state, scheduling, and plain-text report builder."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from config import TRACKED_INSTRUMENTS
from services.schedulers.weekly_schedule import get_week_key, is_weekly_schedule_due, resolve_local_time

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


def mark_weekly_digest_sent(state: WeeklyDigestState, timezone_name: str, now_utc: Optional[datetime] = None) -> None:
    """Mark digest as sent for current local week."""
    now_utc = now_utc or datetime.utcnow()
    now_local = resolve_local_time(now_utc=now_utc, timezone_name=timezone_name)
    state.last_sent_week_key = get_week_key(now_local)
    state.last_sent_timestamp = now_local.isoformat()


def build_weekly_digest_text(
    *,
    state: WeeklyDigestState,
    market_df: pd.DataFrame,
    portfolio_value: Optional[float],
    portfolio_drop_pct: Optional[float],
    buying_ladder_appendix: Optional[str] = None,
) -> str:
    """Build plain-text weekly digest body."""
    lines: List[str] = []
    lines.append("Weekly Investment System Digest")
    lines.append("")
    lines.append("1) Previous week summary")
    lines.append(f"- Triggered market drawdown alerts: {state.alert_counts.get('market_drawdown', 0)}")
    lines.append(f"- Triggered portfolio drop alerts: {state.alert_counts.get('portfolio_drop', 0)}")
    lines.append(f"- Triggered VIX spike alerts: {state.alert_counts.get('vix_spike', 0)}")
    lines.append(f"- Biggest VIX observed: {_format_number(state.max_vix)}")
    lines.append("- Biggest drawdowns observed:")
    for symbol in ["VWCE", "CNDX", "SPY", "QQQ", "VIX", "DXY", "TNX"]:
        dd = state.max_drawdown_by_symbol.get(symbol)
        lines.append(f"  - {symbol}: {_format_percent(dd)}")
    lines.append(f"- Portfolio drop alert occurred: {'yes' if state.portfolio_drop_occurred else 'no'}")
    if state.notable_events:
        lines.append("- Notable events:")
        for event in state.notable_events[-5:]:
            lines.append(f"  - {event}")
    else:
        lines.append("- Notable events: no significant system events captured.")

    lines.append("")
    lines.append("2) Current state")
    lines.append(f"- Current portfolio value: {_format_number(portfolio_value)}")
    lines.append(f"- Current portfolio drawdown vs peak: {_format_percent(portfolio_drop_pct)}")
    lines.append("- Tracked symbols status:")
    for symbol in TRACKED_INSTRUMENTS.keys():
        row = _row_for_symbol(market_df, symbol)
        if row is None:
            lines.append(f"  - {symbol}: data unavailable")
            continue
        lines.append(
            "  - "
            f"{symbol}: price={_format_number(_safe_float(row.get('Price')))} "
            f"daily={_format_percent(_safe_float(row.get('Daily Change %')))} "
            f"drawdown={_format_percent(_safe_float(row.get('Drawdown from ATH %')))}"
        )

    regime = _summarize_market_regime(market_df)
    lines.append(f"- Market regime summary: {regime}")

    lines.append("")
    lines.append("3) Recommended actions (rules-based, not financial advice)")
    for recommendation in _build_recommendations(market_df, state):
        lines.append(f"- Suggested action: {recommendation['action']}")
        lines.append(f"  Reason: {recommendation['reason']}")
        lines.append(f"  Optional amount increase: {recommendation['increase']}")

    if buying_ladder_appendix and buying_ladder_appendix.strip():
        lines.append("")
        lines.extend(buying_ladder_appendix.strip().splitlines())

    return "\n".join(lines)


def _build_recommendations(market_df: pd.DataFrame, state: WeeklyDigestState) -> List[Dict[str, str]]:
    drawdown_symbols = {"VWCE", "CNDX", "SPY", "QQQ"}
    worst_drawdown = None

    for symbol in drawdown_symbols:
        row = _row_for_symbol(market_df, symbol)
        if row is None:
            continue
        drawdown = _safe_float(row.get("Drawdown from ATH %"))
        if drawdown is None:
            continue
        if worst_drawdown is None or drawdown < worst_drawdown:
            worst_drawdown = drawdown

    if worst_drawdown is not None and worst_drawdown <= -40:
        return [
            {
                "action": "Increase staged buying cadence during deep drawdown.",
                "reason": f"Worst equity/ETF drawdown is {worst_drawdown:.2f}%, at or below 40%.",
                "increase": "+150 to +250 (staged, risk-managed)",
            }
        ]
    if worst_drawdown is not None and worst_drawdown <= -30:
        return [
            {
                "action": "Consider staged buffer deployment.",
                "reason": f"Worst equity/ETF drawdown is {worst_drawdown:.2f}%, at or below 30%.",
                "increase": "+100 to +200 (staged deployment)",
            }
        ]
    if worst_drawdown is not None and worst_drawdown <= -20:
        return [
            {
                "action": "Consider increasing monthly contribution.",
                "reason": f"Worst equity/ETF drawdown is {worst_drawdown:.2f}%, at or below 20%.",
                "increase": "+100 to +150",
            }
        ]

    stagnation = (
        state.alert_counts.get("market_drawdown", 0) == 0
        and state.alert_counts.get("portfolio_drop", 0) == 0
        and (state.max_vix is None or state.max_vix < 22)
    )
    if stagnation:
        return [
            {
                "action": "Stay disciplined and consider moderate contribution increase if desired.",
                "reason": "No major drawdown/portfolio stress signals during the week (prolonged calm/stagnation).",
                "increase": "+100 to +200 (optional)",
            }
        ]

    return [
        {
            "action": "Stay with the existing monthly plan.",
            "reason": "No strong drawdown trigger for contribution adjustment was detected.",
            "increase": "No increase required",
        }
    ]


def _summarize_market_regime(market_df: pd.DataFrame) -> str:
    if market_df is None or market_df.empty:
        return "insufficient data"

    vix_row = _row_for_symbol(market_df, "VIX")
    vix = _safe_float(vix_row.get("Price")) if vix_row is not None else None
    worst_drawdown = None
    for symbol in ["VWCE", "CNDX", "SPY", "QQQ"]:
        row = _row_for_symbol(market_df, symbol)
        drawdown = _safe_float(row.get("Drawdown from ATH %")) if row is not None else None
        if drawdown is None:
            continue
        if worst_drawdown is None or drawdown < worst_drawdown:
            worst_drawdown = drawdown

    if vix is not None and vix >= 30:
        return "high-volatility stress regime"
    if worst_drawdown is not None and worst_drawdown <= -20:
        return "risk-off drawdown regime"
    return "normal-to-moderate risk regime"


def _row_for_symbol(market_df: pd.DataFrame, symbol: str) -> Optional[pd.Series]:
    if market_df is None or market_df.empty or "Symbol" not in market_df.columns:
        return None
    rows = market_df.loc[market_df["Symbol"] == symbol]
    if rows.empty:
        return None
    return rows.iloc[0]


def _safe_float(value: object) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def _format_percent(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"
