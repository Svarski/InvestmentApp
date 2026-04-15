"""Weekly digest state, scheduling, and HTML report builder."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
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


def build_weekly_digest_html(
    *,
    state: WeeklyDigestState,
    market_df: pd.DataFrame,
    portfolio_value: Optional[float],
    portfolio_drop_pct: Optional[float],
    buying_ladder_appendix: Optional[str] = None,
) -> str:
    """Build premium, scannable HTML weekly digest body for email clients."""
    # Intentionally unused for now: kept in signature for compatibility with caller contract.
    _ = (portfolio_value, portfolio_drop_pct)
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=7)
    week_range = f"{week_start.strftime('%b %d, %Y')} - {today.strftime('%b %d, %Y')}"
    recommendations = _build_recommendations(market_df, state)
    primary_recommendation = recommendations[0] if recommendations else {
        "action": "Stay with the existing monthly plan.",
        "reason": "No strong drawdown trigger for contribution adjustment was detected.",
        "increase": "No increase required",
    }
    regime = _summarize_market_regime(market_df)
    regime_label = _format_regime_label(regime)
    quick_summary = _build_quick_summary(regime=regime, recommendation=primary_recommendation)
    status_signal = _derive_status_signal(regime=regime, recommendation=primary_recommendation)
    confidence_hint = _build_confidence_hint(regime=regime, recommendation=primary_recommendation)
    action_line, action_optional = _compress_recommendation_copy(primary_recommendation)
    what_matters_now = _build_what_matters_now(regime=regime, recommendation=primary_recommendation)
    ladder_rows = _parse_buying_ladder_appendix(buying_ladder_appendix)

    summary_items = [
        ("Market alerts", str(state.alert_counts.get("market_drawdown", 0))),
        ("Portfolio alerts", str(state.alert_counts.get("portfolio_drop", 0))),
        ("VIX spikes", str(state.alert_counts.get("vix_spike", 0))),
    ]

    drawdown_rows = []
    for symbol in ["VWCE", "CNDX", "SPY", "QQQ", "VIX", "DXY", "TNX"]:
        drawdown_rows.append((symbol, _format_percent(state.max_drawdown_by_symbol.get(symbol))))

    asset_cards = []
    for symbol in ["VWCE", "CNDX", "SPY"]:
        row = _row_for_symbol(market_df, symbol)
        if row is None:
            asset_cards.append({"symbol": symbol, "price": "N/A", "daily": "N/A", "drawdown": "N/A"})
            continue
        asset_cards.append(
            {
                "symbol": symbol,
                "price": _format_currency(_safe_float(row.get("Price"))),
                "daily": _format_percent(_safe_float(row.get("Daily Change %"))),
                "drawdown": _format_percent(_safe_float(row.get("Drawdown from ATH %"))),
            }
        )

    summary_html = "".join(
        "<tr>"
        f"<td style='padding:8px 0; font-size:14px; color:#666;'>{escape(label)}</td>"
        f"<td align='right' style='padding:8px 0; font-size:16px; color:#111; font-weight:700;'>{escape(value)}</td>"
        "</tr>"
        for label, value in summary_items
    )

    drawdown_html = "".join(
        "<tr>"
        f"<td style='padding:10px 12px; border-bottom:1px solid #f0f2f5; font-size:13px; color:#111;'>{escape(symbol)}</td>"
        f"<td align='right' style='padding:10px 12px; border-bottom:1px solid #f0f2f5; font-size:13px; font-weight:700; color:{_metric_color(drawdown)}; font-family:Consolas,Monaco,monospace;'>{escape(drawdown)}</td>"
        "</tr>"
        for symbol, drawdown in drawdown_rows
    )

    cards_html = "".join(
        "<td style='width:33.33%; padding:0 6px 12px; vertical-align:top; min-width:170px;'>"
        "<div style='border:1px solid #e8edf3; border-radius:12px; padding:16px; background:#ffffff;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0'>"
        "<tr>"
        f"<td style='font-size:15px; font-weight:700; color:#111; padding:0 0 10px;'>{escape(card['symbol'])}</td>"
        f"<td align='right' style='font-size:16px; font-weight:700; color:#111; padding:0 0 10px; white-space:nowrap;'>{escape(card['price'])}</td>"
        "</tr></table>"
        f"<div style='font-size:17px; color:{_metric_color(card['daily'])}; font-weight:800; margin-bottom:6px;'>{escape(card['daily'])}</div>"
        f"<div style='font-size:12px; color:#666;'><span style='color:{_metric_color(card['drawdown'])}; font-weight:700;'>{escape(card['drawdown'])}</span> ATH</div>"
        "</div>"
        "</td>"
        for card in asset_cards
    )

    ladder_html = "".join(
        "<tr>"
        f"<td style='padding:10px 12px; border-bottom:1px solid #f0f2f5; font-size:13px; color:#666;'>{escape(metric)}</td>"
        f"<td align='right' style='padding:10px 12px; border-bottom:1px solid #f0f2f5; font-size:13px; color:#111; font-weight:700;'>{escape(value)}</td>"
        "</tr>"
        for metric, value in ladder_rows
    )

    return (
        "<!doctype html>"
        "<html><body style='margin:0; padding:0; background:#f5f7fa; font-family:Arial,Helvetica,sans-serif; color:#111;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#f5f7fa; padding:26px 12px;'>"
        "<tr><td align='center'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='width:100%; max-width:760px; background:#ffffff; border:1px solid #e6ebf1; border-radius:14px;'>"
        "<tr><td style='padding:28px 24px 0;'>"
        "<div style='font-size:32px; line-height:1.2; font-weight:800; color:#111; margin-bottom:8px;'>📊 Weekly Investment Summary</div>"
        f"<div style='font-size:14px; color:#666;'>Last week: {escape(week_range)}</div>"
        "<div style='height:1px; background:#e8edf4; margin-top:18px;'></div>"
        "</td></tr>"
        "<tr><td style='padding:16px 24px 0;'>"
        "<div style='background:#eef5ff; border:1px solid #c8dcfa; border-left:5px solid #2E86DE; border-radius:10px; padding:18px 20px;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:8px;'><tr>"
        "<td style='font-size:12px; color:#2E86DE; font-weight:800; letter-spacing:0.3px; text-transform:uppercase;'>Quick Summary</td>"
        f"<td align='right' style='font-size:12px; color:#2E86DE; font-weight:700;'>{escape(status_signal)}</td>"
        "</tr></table>"
        f"<div style='font-size:18px; line-height:1.3; color:#111; font-weight:800; margin-bottom:6px;'>{escape(quick_summary)}</div>"
        f"<div style='font-size:12px; color:#66758a;'>{escape(confidence_hint)}</div>"
        "</div>"
        "</td></tr>"
        "<tr><td style='padding:20px 24px 0;'><div style='height:1px; background:#edf1f6;'></div></td></tr>"
        "<tr><td style='padding:24px 24px 0;'>"
        "<div style='font-size:20px; line-height:1.25; font-weight:700; color:#111; margin:0 0 14px;'>Previous Week Summary</div>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='margin-bottom:16px;'>"
        f"{summary_html}</table>"
        "<div style='font-size:14px; color:#666; margin:0 0 8px;'>Biggest drawdowns</div>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse; border:1px solid #eef2f6; border-radius:10px; overflow:hidden;'>"
        "<tr><th align='left' style='padding:10px 12px; background:#f9fbfd; border-bottom:1px solid #f0f2f5; color:#666; font-size:12px; font-weight:600;'>Symbol</th>"
        "<th align='right' style='padding:10px 12px; background:#f9fbfd; border-bottom:1px solid #f0f2f5; color:#666; font-size:12px; font-weight:600;'>Drawdown</th></tr>"
        f"{drawdown_html}</table>"
        "</td></tr>"
        "<tr><td style='padding:20px 24px 0;'><div style='height:1px; background:#edf1f6;'></div></td></tr>"
        "<tr><td style='padding:24px 18px 0;'>"
        "<div style='font-size:20px; line-height:1.25; font-weight:700; color:#111; margin:0 6px 14px;'>Current Market State</div>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='table-layout:fixed;'><tr>"
        f"{cards_html}"
        "</tr></table>"
        "</td></tr>"
        "<tr><td style='padding:20px 24px 0;'><div style='height:1px; background:#edf1f6;'></div></td></tr>"
        "<tr><td style='padding:24px 24px 0;'>"
        "<div style='font-size:20px; line-height:1.25; font-weight:700; color:#111; margin:0 0 12px;'>Market Regime</div>"
        "<div style='background:#f5f9ff; border:1px solid #d9e8fb; border-radius:10px; padding:16px; font-size:15px; color:#111;'>"
        f"Market regime: <strong>{escape(regime_label)}</strong>"
        "</div>"
        "</td></tr>"
        "<tr><td style='padding:20px 24px 0;'><div style='height:1px; background:#edf1f6;'></div></td></tr>"
        "<tr><td style='padding:24px 24px 0;'>"
        "<div style='font-size:20px; line-height:1.25; font-weight:700; color:#111; margin:0 0 12px;'>Recommended Action</div>"
        "<div style='background:#f3f8ff; border:1px solid #c9dcf9; border-left:5px solid #2E86DE; border-radius:10px; padding:18px;'>"
        f"<div style='font-size:23px; line-height:1.25; font-weight:800; color:#111; margin-bottom:8px;'>{escape(action_line)}</div>"
        f"<div style='font-size:14px; color:#2E86DE; font-weight:700;'>{escape(action_optional)}</div>"
        "</div>"
        "</td></tr>"
        "<tr><td style='padding:20px 24px 0;'><div style='height:1px; background:#edf1f6;'></div></td></tr>"
        "<tr><td style='padding:24px 24px 16px;'>"
        "<div style='font-size:16px; line-height:1.25; font-weight:700; color:#666; margin:0 0 10px;'>Investment plan</div>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='border-collapse:collapse; border:1px solid #eef2f6; border-radius:10px; overflow:hidden; background:#fcfdff;'>"
        "<tr><th align='left' style='padding:10px 12px; background:#f9fbfd; border-bottom:1px solid #f0f2f5; color:#666; font-size:12px; font-weight:600;'>Metric</th>"
        "<th align='right' style='padding:10px 12px; background:#f9fbfd; border-bottom:1px solid #f0f2f5; color:#666; font-size:12px; font-weight:600;'>Value</th></tr>"
        f"{ladder_html}</table>"
        "</td></tr>"
        "<tr><td style='padding:20px 24px 0;'><div style='height:1px; background:#edf1f6;'></div></td></tr>"
        "<tr><td style='padding:0 24px 12px;'>"
        "<div style='font-size:16px; line-height:1.25; font-weight:700; color:#111; margin:0 0 8px;'>What matters now</div>"
        "<div style='background:#f8fafd; border:1px solid #e8edf3; border-radius:10px; padding:12px 14px; font-size:14px; color:#111; line-height:1.35;'>"
        f"{escape(what_matters_now)}"
        "</div>"
        "</td></tr>"
        "<tr><td style='padding:8px 24px 24px;'>"
        "<div style='font-size:12px; color:#666; border-top:1px solid #eef2f6; padding-top:14px;'>Rules-based system. Long-term investing.</div>"
        "</td></tr>"
        "</table>"
        "</td></tr></table>"
        "</body></html>"
    )


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


def build_daily_digest_message(market_df: pd.DataFrame, state: WeeklyDigestState) -> str:
    """Build a short Telegram-ready daily digest message (1-2 lines)."""
    regime = _summarize_market_regime(market_df)
    recommendation = _build_recommendations(market_df, state)[0]
    action = recommendation.get("action", "").lower()
    worst_drawdown = _get_worst_equity_drawdown(market_df)

    if "high-volatility" in regime.lower() or "deep drawdown" in action:
        line1 = "🔴 Drawdown building. Opportunity increasing."
    elif "risk-off" in regime.lower() or "buffer deployment" in action or "staged buying cadence" in action:
        line1 = "🟡 Mild pullback. Optional increase."
    else:
        line1 = "🟢 Market stable. No action."

    if worst_drawdown is None:
        return line1

    signal_symbol = _select_daily_signal_symbol(market_df)
    line2 = f"{signal_symbol} {worst_drawdown:.2f}% drawdown."
    return f"{line1}\n{line2}"


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


def _get_worst_equity_drawdown(market_df: pd.DataFrame) -> Optional[float]:
    worst_drawdown = None
    for symbol in ["VWCE", "CNDX", "SPY", "QQQ"]:
        row = _row_for_symbol(market_df, symbol)
        drawdown = _safe_float(row.get("Drawdown from ATH %")) if row is not None else None
        if drawdown is None:
            continue
        if worst_drawdown is None or drawdown < worst_drawdown:
            worst_drawdown = drawdown
    return worst_drawdown


def _select_daily_signal_symbol(market_df: pd.DataFrame) -> str:
    best_symbol = "VWCE"
    best_drawdown = None
    for symbol in ["VWCE", "CNDX", "SPY"]:
        row = _row_for_symbol(market_df, symbol)
        drawdown = _safe_float(row.get("Drawdown from ATH %")) if row is not None else None
        if drawdown is None:
            continue
        if best_drawdown is None or drawdown < best_drawdown:
            best_drawdown = drawdown
            best_symbol = symbol
    return best_symbol


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


def _format_currency(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f} EUR"


def _metric_color(text_value: str) -> str:
    if text_value == "N/A":
        return "#666"
    if text_value.startswith("-"):
        return "#e74c3c"
    if text_value.startswith("+") or text_value == "0.00%":
        return "#2ecc71"
    return "#666"


def _parse_buying_ladder_appendix(appendix: Optional[str]) -> List[tuple[str, str]]:
    default_rows: List[tuple[str, str]] = [
        ("Phase", "N/A"),
        ("Base", "N/A"),
        ("Recommended", "N/A"),
        ("Drawdown", "N/A"),
    ]
    if not appendix or not appendix.strip():
        return default_rows

    extracted: Dict[str, str] = {}
    for line in appendix.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        content = stripped[2:]
        if ":" not in content:
            continue
        key, value = content.split(":", 1)
        extracted[key.strip()] = value.strip()

    return [
        ("Phase", extracted.get("Active phase", "N/A")),
        ("Base", extracted.get("Base monthly", "N/A")),
        ("Recommended", extracted.get("Recommended now", "N/A")),
        ("Drawdown", extracted.get("Drawdown (ATH)", "N/A")),
    ]


def _build_quick_summary(regime: str, recommendation: Dict[str, str]) -> str:
    regime_lower = regime.lower()
    action_lower = recommendation.get("action", "").lower()
    if "high-volatility" in regime_lower or "deep drawdown" in action_lower:
        return "Deeper correction. Gradual increases may be justified."
    if "risk-off" in regime_lower or "buffer deployment" in action_lower or "staged buying cadence" in action_lower:
        return "Mild opportunity building. Optional increase can be considered."
    return "Market stable. Stay with the plan."


def _compress_recommendation_copy(recommendation: Dict[str, str]) -> tuple[str, str]:
    action = recommendation.get("action", "")
    increase = recommendation.get("increase", "No increase required")
    action_lower = action.lower()
    if "deep drawdown" in action_lower:
        return "Increase exposure gradually", f"Optional: {increase}"
    if "buffer deployment" in action_lower:
        return "Increase exposure gradually", f"Optional: {increase}"
    if "increasing monthly contribution" in action_lower:
        return "Increase contribution", f"Optional: {increase}"
    if "stay disciplined" in action_lower:
        return "Stay consistent", f"Optional: {increase}"
    if "existing monthly plan" in action_lower:
        return "Stay consistent", "Optional: keep current contribution"
    return action, f"Optional: {increase}"


def _derive_status_signal(regime: str, recommendation: Dict[str, str]) -> str:
    regime_lower = regime.lower()
    action_lower = recommendation.get("action", "").lower()
    if "high-volatility" in regime_lower or "deep drawdown" in action_lower:
        return "🔴 Opportunity"
    if "risk-off" in regime_lower or "buffer deployment" in action_lower or "staged buying cadence" in action_lower:
        return "🟡 Caution"
    return "🟢 Stable"


def _build_what_matters_now(regime: str, recommendation: Dict[str, str]) -> str:
    regime_lower = regime.lower()
    action_lower = recommendation.get("action", "").lower()
    if "high-volatility" in regime_lower:
        return "Volatility rising. Watch for opportunities."
    if "risk-off" in regime_lower or "deep drawdown" in action_lower:
        return "Early drawdown phase. Build positions gradually."
    if "increase" in action_lower:
        return "No stress signals. Accumulate steadily."
    return "Calm market. Stay disciplined."


def _build_confidence_hint(regime: str, recommendation: Dict[str, str]) -> str:
    regime_lower = regime.lower()
    action_lower = recommendation.get("action", "").lower()
    if "high-volatility" in regime_lower:
        return "Signal environment: high volatility"
    if "risk-off" in regime_lower or "deep drawdown" in action_lower:
        return "Signal environment: early drawdown phase"
    if "buffer deployment" in action_lower or "staged buying cadence" in action_lower:
        return "Signal environment: mild opportunity"
    return "Signal environment: calm / low signal"


def _format_regime_label(regime: str) -> str:
    regime_lower = regime.lower()
    if "normal-to-moderate risk regime" in regime_lower:
        return "Normal / Moderate risk"
    if "risk-off drawdown regime" in regime_lower:
        return "Risk-off / Drawdown"
    if "high-volatility stress regime" in regime_lower:
        return "High volatility"
    return regime.title()
