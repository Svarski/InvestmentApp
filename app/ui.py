"""Main UI rendering functions for the dashboard — fintech redesign."""
from __future__ import annotations

import html
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from zoneinfo import ZoneInfo

from alerts import AlertEngine, get_alert_settings
from app.components import format_percent, format_price, render_warning_messages
from buying_ladder.allocation import compute_vwce_cndx_split
from buying_ladder.ui import render_buying_ladder_sidebar
from buying_ladder.logic import compute_buying_ladder
from buying_ladder.models import merge_with_defaults
from buying_ladder.storage import load_buying_ladder_settings
from config import DEFAULT_LOOKBACK_PERIOD
from db import get_latest_portfolio_snapshot, get_portfolio_history, get_recent_alerts
from services.market_data import build_market_overview
from services.portfolio_sync import load_portfolio_sync_state

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT_MAX_AGE_SEC = 300

_HEADER_REMINDERS: List[str] = [
    "Keep investing",
    "Don't stop contributions",
    "Market down = opportunity",
    "Stay consistent",
    "Don't sell in a crash",
    "Ignore the noise",
    "Discipline beats timing",
    "Think long term",
    "Stick to the plan",
    "Volatility is normal",
]

_GLOBAL_CSS = """
<style>
[data-testid="stAppViewContainer"] { background: #F7F8FA; }
[data-testid="stSidebar"] { background: #FFFFFF; border-right: 1px solid #E8EAF0; }
section.main > div { padding-top: 1.5rem !important; }

/* Reduce top padding */
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 1rem;
    padding-left: 1rem;
    padding-right: 1rem;
}

/* Remove default header spacing */
header[data-testid="stHeader"] {
    height: 0rem;
}

/* Slight upward shift (mobile fix) */
.stApp {
    margin-top: -20px;
}

/* Mobile-specific tweak */
@media (max-width: 768px) {
    .block-container {
        padding-top: 1rem;
    }
}
.ft-card { background: #FFFFFF; border: 1px solid #E8EAF0; border-radius: 14px; padding: 20px 24px; margin-bottom: 14px; }
.hero-card { background: #EEF3F0; border-radius: 16px; padding: 28px 28px 22px; margin-bottom: 14px; }
.hero-label { font-size: 11px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; color: #64748B; margin-bottom: 10px; }
.hero-amount { font-size: 42px; font-weight: 700; color: #0F172A; letter-spacing: -0.03em; line-height: 1.1; }
.hero-split { font-size: 14px; color: #94A3B8; margin-top: 6px; margin-bottom: 16px; }
.hero-action { display: inline-flex; align-items: center; gap: 6px; background: #22C55E; color: #FFFFFF; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 99px; }
.hero-action-caution { background: #F59E0B; }
.hero-disclaimer { font-size: 11px; color: #475569; margin-top: 14px; }
.status-pill { display: inline-flex; align-items: center; gap: 7px; padding: 6px 14px; border-radius: 99px; font-size: 13px; font-weight: 500; margin-bottom: 16px; }
.status-ok { background: #DCFCE7; color: #15803D; }
.status-warn { background: #FEF9C3; color: #854D0E; }
.status-err { background: #FEE2E2; color: #B91C1C; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: #22C55E; }
.dot-warn { background: #F59E0B; }
.dot-err { background: #EF4444; }
.section-title { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: #64748B; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #F1F5F9; }
.metrics-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.metric-box { background: #F8FAFC; border: 1px solid #E8EAF0; border-radius: 10px; padding: 12px 14px; }
.metric-label { font-size: 11px; color: #64748B; margin-bottom: 4px; font-weight: 500; }
.metric-value { font-size: 18px; font-weight: 600; color: #0F172A; letter-spacing: -.02em; }
.metric-value.large { font-size: 24px; }
.metric-sub { font-size: 11px; color: #94A3B8; margin-top: 2px; }
.alloc-bar-wrap { height: 6px; background: #F1F5F9; border-radius: 3px; overflow: hidden; display: flex; margin: 10px 0 6px; }
.alloc-seg-vwce { background: #3B82F6; }
.alloc-seg-cndx { background: #8B5CF6; }
.alloc-seg-cash { background: #D1D5DB; }
.alloc-legend { display: flex; gap: 14px; flex-wrap: wrap; }
.leg-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #64748B; }
.leg-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.mkt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 6px; }
.mkt-card { background: #F8FAFC; border: 1px solid #E8EAF0; border-radius: 10px; padding: 14px 16px; }
.mkt-sym { font-size: 12px; font-weight: 600; color: #64748B; letter-spacing: .04em; }
.mkt-desc { font-size: 11px; color: #94A3B8; margin-bottom: 6px; }
.mkt-price { font-size: 22px; font-weight: 700; color: #0F172A; letter-spacing: -.02em; }
.mkt-badge { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 99px; margin: 4px 0; }
.badge-up { background: #DCFCE7; color: #15803D; }
.badge-down { background: #FEE2E2; color: #B91C1C; }
.badge-flat { background: #F1F5F9; color: #64748B; }
.mkt-dd { font-size: 11px; color: #94A3B8; margin-top: 4px; }
.plan-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.plan-item { background: #F8FAFC; border: 1px solid #E8EAF0; border-radius: 10px; padding: 12px 14px; }
.plan-key { font-size: 11px; color: #64748B; font-weight: 500; }
.plan-val { font-size: 16px; font-weight: 600; color: #0F172A; margin-top: 4px; }
.alert-box { border-radius: 10px; padding: 12px 16px; font-size: 13px; font-weight: 500; margin-bottom: 8px; display: flex; align-items: flex-start; gap: 10px; }
.alert-icon { font-size: 14px; flex-shrink: 0; margin-top: 1px; }
.alert-err { background: #FEF2F2; border: 1px solid #FECACA; color: #991B1B; }
.alert-warn { background: #FFFBEB; border: 1px solid #FDE68A; color: #92400E; }
.alert-info { background: #EFF6FF; border: 1px solid #BFDBFE; color: #1E40AF; }
.alert-ok { background: #F0FDF4; border: 1px solid #BBF7D0; color: #14532D; }
.ft-divider { height: 1px; background: #F1F5F9; margin: 20px 0; }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
header { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }
.plan-visual-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 4px; }
.plan-visual-card {
    background: #FFFFFF;
    border: 1px solid #E8EAF0;
    border-radius: 18px;
    padding: 20px 22px;
    min-height: 150px;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.plan-visual-title {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 18px;
    font-weight: 700;
    color: #1E293B;
    margin-bottom: 18px;
}
.plan-visual-icon {
    width: 30px;
    height: 30px;
    border-radius: 10px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    background: #F1F5F9;
    color: #64748B;
    flex-shrink: 0;
}
.plan-visual-label {
    font-size: 13px;
    color: #64748B;
    margin-bottom: 6px;
}
.plan-visual-big {
    font-size: 28px;
    line-height: 1.15;
    font-weight: 700;
    color: #0F172A;
    letter-spacing: -0.03em;
}
.plan-visual-sub {
    font-size: 14px;
    color: #64748B;
    margin-top: 8px;
}
.plan-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 14px;
}
.plan-pill-ok {
    background: #DCFCE7;
    color: #166534;
}
.plan-row-inline {
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
}
.plan-accent-blue { color: #3B82F6; }
.plan-accent-purple { color: #818CF8; }
.how-card {
    background: #FFFFFF;
    border: 1px solid #E8EAF0;
    border-radius: 18px;
    overflow: hidden;
    margin-top: 18px;
}
.how-body {
    padding: 18px 22px 10px;
}
.how-list {
    list-style: none;
    padding: 0;
    margin: 0;
}
.how-item {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 0;
    border-top: 1px solid #F8FAFC;
}
.how-item:first-child {
    border-top: 0;
}
.how-dot {
    width: 18px;
    height: 18px;
    border-radius: 999px;
    flex-shrink: 0;
    margin-top: 2px;
}
.how-dot-blue { background: #60A5FA; }
.how-dot-green { background: #34D399; }
.how-dot-slate {
    background: #E2E8F0;
    color: #64748B;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
}
.how-dot-purple { background: #818CF8; }
.how-text {
    font-size: 15px;
    line-height: 1.5;
    color: #334155;
}
.how-text strong {
    color: #0F172A;
}
</style>
"""

_PRIMARY_MARKET_SYMBOLS = frozenset({"VWCE", "CNDX"})

_SYMBOL_DESCRIPTIONS: Dict[str, str] = {
    "VWCE": "Global all-world ETF",
    "CNDX": "Nasdaq 100",
    "SPY":  "S&P 500",
    "VIX":  "Volatility index",
    "DXY":  "US Dollar index",
    "TNX":  "10Y Treasury yield",
}


def _vwce_drawdown_pct(market_df: Optional[pd.DataFrame]) -> Optional[float]:
    if market_df is None or market_df.empty:
        return None
    if "Symbol" not in market_df.columns or "Drawdown from ATH %" not in market_df.columns:
        return None
    sym = market_df["Symbol"].astype(str).str.upper().str.strip()
    rows = market_df[sym == "VWCE"]
    if rows.empty:
        return None
    val = rows["Drawdown from ATH %"].iloc[0]
    if val is None or pd.isna(val):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _context_reminder_from_drawdown(drawdown: Optional[float]) -> Optional[str]:
    if drawdown is None:
        return None
    if drawdown <= -20:
        return "Strong buying opportunity — stay aggressive"
    if drawdown <= -10:
        return "Market dip — consider increasing exposure"
    if drawdown < 0:
        return "Market slightly down — stay consistent"
    return "Market stable — keep investing"


def _daily_rotating_reminder() -> str:
    idx = datetime.now().timetuple().tm_yday % len(_HEADER_REMINDERS)
    return _HEADER_REMINDERS[idx]


def _header_reminder_caption_text(market_df: Optional[pd.DataFrame]) -> str:
    ctx = _context_reminder_from_drawdown(_vwce_drawdown_pct(market_df))
    return ctx if ctx is not None else _daily_rotating_reminder()


def _action_info_from_drawdown(drawdown_pct: Optional[float]) -> Tuple[str, str]:
    if drawdown_pct is None or drawdown_pct >= -5:
        return "Keep investing", "hero-action"
    if drawdown_pct <= -10:
        return "Increase exposure — market dip", "hero-action hero-action-caution"
    return "Market slightly down — stay consistent", "hero-action"


def _status_pill(text: str, kind: str = "ok") -> str:
    cls_pill = {"ok": "status-ok", "warn": "status-warn", "err": "status-err"}.get(kind, "status-ok")
    cls_dot  = {"ok": "dot-ok",   "warn": "dot-warn",   "err": "dot-err"}.get(kind, "dot-ok")
    return (
        f'<div class="status-pill {cls_pill}">'
        f'<span class="status-dot {cls_dot}"></span>{html.escape(text)}'
        f'</div>'
    )


def _split_market_display_df(display_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if display_df.empty or "Symbol" not in display_df.columns:
        return display_df, pd.DataFrame()
    sym     = display_df["Symbol"].astype(str).str.upper().str.strip()
    primary = display_df[sym.isin(_PRIMARY_MARKET_SYMBOLS)].copy()
    rest    = display_df[~sym.isin(_PRIMARY_MARKET_SYMBOLS)].copy()
    return primary, rest


def _format_portfolio_eur(value: float) -> str:
    return f"{round(value, 2):,.2f} €"


def _portfolio_alloc_pct(part: float, total: float) -> float:
    if total == 0 or not math.isfinite(total) or not math.isfinite(part):
        return 0.0
    return round(part / total * 100, 1)


def _parse_iso_to_utc(value: object) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _format_sync_time_local(value: object) -> Optional[str]:
    dt_utc = _parse_iso_to_utc(value)
    if dt_utc is None:
        return None
    try:
        local_tz = ZoneInfo("Europe/Ljubljana")
    except Exception:
        local_tz = timezone.utc
    return dt_utc.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")


def _portfolio_sync_status_line() -> str:
    try:
        state = load_portfolio_sync_state()
    except Exception:
        logger.exception("Failed to read portfolio sync state.")
        return "No sync data yet"
    if not isinstance(state, dict):
        return "No sync data yet"

    status = str(state.get("status") or "unknown").strip().lower()
    last_success_raw = state.get("last_successful_sync")
    last_attempt_raw = state.get("last_attempt")

    last_success_local = _format_sync_time_local(last_success_raw)
    last_attempt_local = _format_sync_time_local(last_attempt_raw)
    last_success_utc = _parse_iso_to_utc(last_success_raw)

    if status == "in_progress":
        if last_success_local:
            return f"⏳ Sync in progress... last good data: {last_success_local}"
        return "⏳ Sync in progress... no previous data yet"

    if status == "failed":
        if last_success_local:
            if last_attempt_local:
                return (
                    f"🔴 Last sync failed (last attempt: {last_attempt_local}, "
                    f"showing data from {last_success_local})"
                )
            return f"🔴 Last sync failed (showing data from {last_success_local})"
        return "🔴 Last sync failed (no valid portfolio data yet)"

    if status not in {"success"}:
        return "No sync data yet"

    if last_success_utc is None:
        return "No sync data yet"

    if (datetime.now(timezone.utc) - last_success_utc) > timedelta(hours=24):
        return f"🟡 Data may be outdated (last sync: {last_success_local or 'unknown'})"

    return f"🟢 Last sync: {last_success_local or 'unknown'} (OK)"


def _parse_last_success_timestamp_utc(value: object) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _heartbeat_success_is_stale(raw: dict) -> bool:
    parsed = _parse_last_success_timestamp_utc(raw.get("last_success_timestamp"))
    if parsed is None:
        return True
    return (datetime.now(timezone.utc) - parsed).total_seconds() > WORKER_HEARTBEAT_MAX_AGE_SEC


def _read_worker_heartbeat_state() -> Tuple[bool, str, Optional[str]]:
    path = (os.getenv("WORKER_HEARTBEAT_FILE") or "").strip()
    if not path:
        return False, "Fallback", None
    if not os.path.isfile(path):
        return True, "Fallback", None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return True, "Fallback", None
    if not isinstance(raw, dict):
        return True, "Fallback", None
    heartbeat_stale = _heartbeat_success_is_stale(raw)
    src = raw.get("portfolio_source") or "Fallback"
    if src not in ("IBKR", "IBKR_STALE", "Fallback"):
        src = "Fallback"
    last_ibkr_line: Optional[str] = None
    ts = raw.get("portfolio_ibkr_timestamp")
    if ts is not None and src in ("IBKR", "IBKR_STALE"):
        try:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            last_ibkr_line = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (TypeError, ValueError, OSError):
            pass
    return heartbeat_stale, src, last_ibkr_line


def _get_alert_engine() -> AlertEngine:
    if "alert_engine" not in st.session_state:
        st.session_state["alert_engine"] = AlertEngine(settings=get_alert_settings())
    return st.session_state["alert_engine"]


def _append_alert_history(new_alerts: list, max_items: int = 50) -> None:
    if "alerts" not in st.session_state:
        st.session_state["alerts"] = []
    history = st.session_state["alerts"]
    existing_keys = {(item.get("id"), item.get("timestamp")) for item in history}
    for alert in new_alerts:
        key = (alert.id, alert.timestamp)
        if key in existing_keys:
            continue
        history.append({"id": alert.id, "type": alert.type, "message": alert.message,
                         "severity": alert.severity, "timestamp": alert.timestamp})
        existing_keys.add(key)
    st.session_state["alerts"] = history[-max_items:]


def _ibkr_total_value_for_alerts() -> Optional[float]:
    try:
        df = get_latest_portfolio_snapshot()
        if df is None or df.empty:
            return None
        v = float(pd.to_numeric(df.iloc[0].get("total_value"), errors="coerce"))
        if math.isfinite(v):
            return v
    except Exception:
        logger.exception("Failed to read IBKR portfolio total for alerts.")
    return None


def _evaluate_alerts_safely(market_df: pd.DataFrame, portfolio_value: Optional[float]) -> None:
    required_columns = {"Symbol", "Drawdown from ATH %", "Price"}
    if market_df is None or market_df.empty or not required_columns.issubset(set(market_df.columns)):
        return
    try:
        engine = _get_alert_engine()
        new_alerts = engine.evaluate(market_df=market_df, portfolio_value=portfolio_value)
        _append_alert_history(new_alerts=new_alerts, max_items=50)
    except Exception:
        logger.exception("Alert evaluation failed.")


def _severity_style(alert: Dict) -> Tuple[str, str]:
    severity = str(alert.get("severity", "")).lower()
    if severity == "high":
        return "alert-err", "▲"
    if severity == "medium":
        return "alert-warn", "●"
    return "alert-info", "○"


def _market_badge(daily_change: Optional[float]) -> str:
    if daily_change is None:
        return '<span class="mkt-badge badge-flat">—</span>'
    sign  = "+" if daily_change >= 0 else ""
    arrow = "▲" if daily_change > 0 else ("▼" if daily_change < 0 else "●")
    css   = "badge-up" if daily_change > 0 else ("badge-down" if daily_change < 0 else "badge-flat")
    return f'<span class="mkt-badge {css}">{arrow} {sign}{daily_change:.2f}%</span>'


# ── Section renderers ─────────────────────────────────────────────────────────

def render_header(market_df: Optional[pd.DataFrame] = None) -> None:
    st.markdown(
        '<p style="font-size:11px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#94A3B8;margin:0 0 4px">Personal finance</p>'
        '<h1 style="font-size:26px;font-weight:700;color:#0F172A;letter-spacing:-0.03em;margin:0 0 4px">Investment Dashboard</h1>'
        '<p style="font-size:13px;color:#64748B;margin:0 0 16px">Stay consistent. Ignore noise. Build long-term wealth.</p>',
        unsafe_allow_html=True,
    )
    reminder = _header_reminder_caption_text(market_df)
    drawdown = _vwce_drawdown_pct(market_df)
    kind = "err" if (drawdown is not None and drawdown <= -20) else ("warn" if (drawdown is not None and drawdown <= -10) else "ok")
    st.markdown(_status_pill(reminder, kind), unsafe_allow_html=True)


def _render_investment_hero(market_df: pd.DataFrame) -> None:
    settings    = merge_with_defaults(load_buying_ladder_settings())
    result      = compute_buying_ladder(settings, market_df)
    recommended = result.recommended_monthly if result.feature_enabled else 0.0
    action_label, action_css = _action_info_from_drawdown(result.drawdown_pct if result.feature_enabled else None)

    alloc_text = ""
    split = compute_vwce_cndx_split(settings, result, market_df)
    if split is not None and split.show_ui_block:
        try:
            vw_amt   = float(split.vwce_amount)
            cndx_amt = float(split.cndx_amount)
            if math.isfinite(vw_amt) and math.isfinite(cndx_amt):
                alloc_text = f"VWCE {vw_amt:,.0f} € · CNDX {cndx_amt:,.0f} €"
        except (TypeError, ValueError):
            pass

    st.markdown(
        f"""<div class="hero-card">
            <div class="hero-label">This month — invest</div>
            <div class="hero-amount">{recommended:,.0f} €</div>
            <div class="hero-split">{html.escape(alloc_text) if alloc_text else "&nbsp;"}</div>
            <div class="{action_css}">{html.escape(action_label)}</div>
            <div class="hero-disclaimer">Guidance only — not investment advice.</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_alerts_section() -> None:
    alerts_history = st.session_state.get("alerts", [])
    if not alerts_history:
        st.markdown(
            '<div class="alert-box alert-ok"><span class="alert-icon">✓</span>'
            '<span>All clear — nothing needs your attention</span></div>',
            unsafe_allow_html=True,
        )
        return
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    sorted_alerts = sorted(
        alerts_history,
        key=lambda a: (severity_rank.get(str(a.get("severity", "")).lower(), 0), str(a.get("timestamp", ""))),
        reverse=True,
    )
    for alert in sorted_alerts[:3]:
        css, icon = _severity_style(alert)
        msg = html.escape(str(alert.get("message", "Alert")))
        st.markdown(
            f'<div class="alert-box {css}"><span class="alert-icon">{icon}</span><span>{msg}</span></div>',
            unsafe_allow_html=True,
        )


def render_portfolio_overview() -> None:
    try:
        df = get_latest_portfolio_snapshot()
    except Exception:
        logger.exception("Failed to load latest portfolio snapshot.")
        st.info("No portfolio data yet")
        return
    if df is None or df.empty:
        st.info("No portfolio data yet")
        return
    row   = df.iloc[0]
    total = float(pd.to_numeric(row.get("total_value"), errors="coerce"))
    vwce  = float(pd.to_numeric(row.get("vwce_value"),  errors="coerce"))
    cndx  = float(pd.to_numeric(row.get("cndx_value"),  errors="coerce"))
    cash  = float(pd.to_numeric(row.get("cash"),         errors="coerce"))
    if any(not math.isfinite(x) for x in (total, vwce, cndx, cash)):
        st.info("No portfolio data yet")
        return
    vwce_pct = _portfolio_alloc_pct(vwce, total)
    cndx_pct = _portfolio_alloc_pct(cndx, total)
    cash_pct = _portfolio_alloc_pct(cash, total)
    sync_status_line = _portfolio_sync_status_line()
    st.markdown(
        f"""<div class="ft-card">
            <div class="section-title">Portfolio</div>
            <div style="margin-bottom:16px">
                <div class="metric-label">Total value</div>
                <div class="metric-value large">{_format_portfolio_eur(total)}</div>
                <div class="metric-sub">{html.escape(sync_status_line)}</div>
            </div>
            <div class="metrics-row">
                <div class="metric-box">
                    <div class="metric-label">VWCE</div>
                    <div class="metric-value">{_format_portfolio_eur(vwce)}</div>
                    <div class="metric-sub">{vwce_pct:.1f}%</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">CNDX</div>
                    <div class="metric-value">{_format_portfolio_eur(cndx)}</div>
                    <div class="metric-sub">{cndx_pct:.1f}%</div>
                </div>
                <div class="metric-box">
                    <div class="metric-label">Cash</div>
                    <div class="metric-value">{_format_portfolio_eur(cash)}</div>
                    <div class="metric-sub">{cash_pct:.1f}% · uninvested</div>
                </div>
            </div>
            <div class="alloc-bar-wrap">
                <div class="alloc-seg-vwce" style="flex:0 0 {vwce_pct}%"></div>
                <div class="alloc-seg-cndx" style="flex:0 0 {cndx_pct}%"></div>
                <div class="alloc-seg-cash" style="flex:1"></div>
            </div>
            <div class="alloc-legend">
                <div class="leg-item"><div class="leg-dot" style="background:#3B82F6"></div>VWCE {vwce_pct:.1f}%</div>
                <div class="leg-item"><div class="leg-dot" style="background:#8B5CF6"></div>CNDX {cndx_pct:.1f}%</div>
                <div class="leg-item"><div class="leg-dot" style="background:#D1D5DB"></div>Cash {cash_pct:.1f}%</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def _render_market_cards_fintech(display_df: pd.DataFrame) -> None:
    if display_df.empty:
        st.info("Market data unavailable.")
        return
    fields = ["Symbol", "Price", "Daily Change %", "Drawdown from ATH %"]
    rows = [r for _, r in display_df[fields].iterrows()]
    cards_html = '<div class="mkt-grid">'
    for row in rows:
        sym      = str(row.get("Symbol") or "")
        desc     = _SYMBOL_DESCRIPTIONS.get(sym.upper(), "")
        price_raw = row.get("Price")
        price_str = format_price(price_raw) if price_raw is not None and not pd.isna(price_raw) else "—"
        daily_raw = row.get("Daily Change %")
        daily_val: Optional[float] = None
        if daily_raw is not None and not pd.isna(daily_raw):
            try:
                v = float(daily_raw)
                daily_val = v if math.isfinite(v) else None
            except (TypeError, ValueError):
                pass
        badge = _market_badge(daily_val)
        dd_raw = row.get("Drawdown from ATH %")
        dd_str = format_percent(dd_raw) if dd_raw is not None else "—"
        cards_html += (
            f'<div class="mkt-card">'
            f'<div class="mkt-sym">{html.escape(sym)}</div>'
            f'<div class="mkt-desc">{html.escape(desc)}</div>'
            f'<div class="mkt-price">{html.escape(price_str)}</div>'
            f'{badge}'
            f'<div class="mkt-dd">{html.escape(dd_str)} from peak</div>'
            f'</div>'
        )
    cards_html += '</div>'
    st.markdown(cards_html, unsafe_allow_html=True)


def _render_plan_details(market_df: pd.DataFrame) -> None:
    settings = merge_with_defaults(load_buying_ladder_settings())
    result = compute_buying_ladder(settings, market_df)

    phase_label = result.phase_label if result.feature_enabled else "—"
    base_monthly = f"{result.base_monthly:,.0f} €" if result.feature_enabled else "—"
    recommended = f"{result.recommended_monthly:,.0f} €" if result.feature_enabled else "—"
    dd_str = f"{result.drawdown_pct:.2f}%" if result.drawdown_pct is not None else "—"
    step_label = result.ladder_step_label if result.feature_enabled else "Normal"

    alloc_ratio = "80% / 20% CNDX"
    vwce_alloc = "—"
    cndx_alloc = "—"

    split = compute_vwce_cndx_split(settings, result, market_df)
    if split is not None and split.show_ui_block:
        try:
            vwce_alloc = f"{float(split.vwce_amount):,.0f} €"
            cndx_alloc = f"{float(split.cndx_amount):,.0f} €"
        except (TypeError, ValueError):
            pass

    pill_html = (
        f'<span class="plan-pill plan-pill-ok">✓ {html.escape(step_label)}</span>'
        if result.feature_enabled else ""
    )

    plan_html = "".join([
        '<div class="plan-visual-grid">',
        '<div class="plan-visual-card">',
        '<div class="plan-visual-title"><span class="plan-visual-icon">▦</span><span>Plan</span></div>',
        f'<div class="plan-visual-big">{html.escape(phase_label)}</div>',
        '<div class="plan-visual-sub">Base monthly</div>',
        f'<div class="plan-visual-big" style="margin-top:10px">{html.escape(base_monthly)}</div>',
        '</div>',
        '<div class="plan-visual-card">',
        '<div class="plan-visual-title"><span class="plan-visual-icon" style="background:#DCFCE7;color:#16A34A">✓</span><span>Invest this month</span></div>',
        f'<div class="plan-visual-big">{html.escape(recommended)}</div>',
        '<div class="plan-visual-sub">Extra to base</div>',
        f'<div class="plan-visual-big" style="margin-top:10px">{result.extra_vs_base:+,.0f} €</div>',
        '</div>',
        '<div class="plan-visual-card">',
        '<div class="plan-visual-title"><span class="plan-visual-icon">↗</span><span>Market</span></div>',
        '<div class="plan-visual-sub">Down from peak</div>',
        f'<div class="plan-row-inline" style="margin-top:8px"><div class="plan-visual-big">{html.escape(dd_str)}</div></div>',
        '</div>',
        '<div class="plan-visual-card">',
        '<div class="plan-visual-title"><span class="plan-visual-icon" style="background:#DCFCE7;color:#16A34A">✓</span><span>Regime</span></div>',
        pill_html,
        '<div class="plan-visual-sub">Ladder step</div>',
        f'<div class="plan-visual-big" style="margin-top:8px">{html.escape(step_label)}</div>',
        '</div>',
        '<div class="plan-visual-card">',
        '<div class="plan-visual-title"><span class="plan-visual-icon" style="background:#DBEAFE;color:#2563EB">●</span><span>Allocation</span></div>',
        f'<div class="plan-visual-sub">{html.escape(alloc_ratio)}</div>',
        f'<div class="plan-row-inline" style="margin-top:8px"><div class="plan-visual-big plan-accent-blue">{html.escape(vwce_alloc)}</div><div class="plan-visual-sub">VWCE</div></div>',
        f'<div class="plan-row-inline" style="margin-top:8px"><div class="plan-visual-big plan-accent-purple">{html.escape(cndx_alloc)}</div><div class="plan-visual-sub">CNDX</div></div>',
        '</div>',
        '</div>',
    ])

    st.markdown(plan_html, unsafe_allow_html=True)
    _render_how_we_got_here(result, split)


def render_portfolio_performance_section() -> None:
    st.markdown(
        '<p style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;'
        'color:#64748B;margin:0 0 4px">Portfolio over time</p>'
        '<p style="font-size:12px;color:#94A3B8;margin:0 0 12px">Using last available data</p>',
        unsafe_allow_html=True,
    )

    period_options = [7, 30, 90]
    period_labels  = ["7 days", "30 days", "90 days"]
    if "perf_period_idx" not in st.session_state:
        st.session_state["perf_period_idx"] = 1

    cols = st.columns(len(period_options))
    for i, (label, _) in enumerate(zip(period_labels, period_options)):
        with cols[i]:
            active = st.session_state["perf_period_idx"] == i
            if st.button(label, key=f"period_btn_{i}",
                         type="primary" if active else "secondary",
                         use_container_width=True):
                st.session_state["perf_period_idx"] = i
                st.rerun()

    days = period_options[st.session_state["perf_period_idx"]]
    df   = get_portfolio_history(days=days)

    if df.empty:
        st.info("Need more history to draw a chart — data appears after your portfolio syncs.")
        return

    df = df.copy()
    df["timestamp"]   = pd.to_datetime(df["timestamp"],  errors="coerce")
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce")
    for col in ("vwce_value", "cndx_value", "cash"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "total_value"]).sort_values("timestamp")

    if df.empty:
        st.info("Need more history to draw a chart.")
        return

    baseline = df["total_value"].iloc[0]
    df["growth_pct"] = 0.0 if (pd.isna(baseline) or baseline == 0) else (df["total_value"] / baseline - 1) * 100

    last_value  = float(df["total_value"].iloc[-1])
    last_growth = float(df["growth_pct"].iloc[-1])

    col1, col2 = st.columns(2)
    col1.metric("Portfolio value", f"{last_value:,.0f} €".replace(",", " "))
    col2.metric("Period return",   f"{last_growth:.2f} %", delta=last_growth)

    if len(df) < 2:
        st.info("Need more history to draw a useful chart.")
        return

    # Value chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["total_value"],
        mode="lines", name="Total",
        line=dict(color="#3B82F6", width=2),
        fill="tozeroy", fillcolor="rgba(59,130,246,0.07)",
        hovertemplate="%{x|%d %b %Y}<br><b>%{y:,.0f} €</b><extra></extra>",
    ))
    if "vwce_value" in df.columns and df["vwce_value"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["vwce_value"],
            mode="lines", name="VWCE",
            line=dict(color="#8B5CF6", width=1.5, dash="dot"),
            hovertemplate="%{x|%d %b %Y}<br>VWCE <b>%{y:,.0f} €</b><extra></extra>",
        ))
    if "cndx_value" in df.columns and df["cndx_value"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=df["cndx_value"],
            mode="lines", name="CNDX",
            line=dict(color="#06B6D4", width=1.5, dash="dot"),
            hovertemplate="%{x|%d %b %Y}<br>CNDX <b>%{y:,.0f} €</b><extra></extra>",
        ))
    fig.update_layout(
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=0, r=0, t=8, b=0), height=220,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=11, color="#64748B"), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=11, color="#94A3B8"), tickformat="%d %b"),
        yaxis=dict(showgrid=True, gridcolor="#F1F5F9", zeroline=False,
                   tickfont=dict(size=11, color="#94A3B8"), ticksuffix=" €"),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # Return % chart
    line_color  = "#22C55E" if last_growth >= 0 else "#EF4444"
    fill_color  = "rgba(34,197,94,0.08)" if last_growth >= 0 else "rgba(239,68,68,0.08)"
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=df["timestamp"], y=df["growth_pct"],
        mode="lines", name="Return %",
        line=dict(color=line_color, width=2),
        fill="tozeroy", fillcolor=fill_color,
        hovertemplate="%{x|%d %b %Y}<br><b>%{y:.2f}%</b><extra></extra>",
    ))
    fig2.add_hline(y=0, line_color="#E2E8F0", line_width=1)
    fig2.update_layout(
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=0, r=0, t=8, b=0), height=140,
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=11, color="#94A3B8"), tickformat="%d %b"),
        yaxis=dict(showgrid=True, gridcolor="#F1F5F9", zeroline=False,
                   tickfont=dict(size=11, color="#94A3B8"), ticksuffix="%"),
        hovermode="x unified",
    )
    st.caption("Return in this period (%)")
    st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})


def render_alert_history_section() -> None:
    df = get_recent_alerts(limit=5)
    if df.empty:
        return
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp", ascending=False)
    df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    df = df[["timestamp", "symbol", "type", "level", "message"]]
    df.columns = ["Time", "Symbol", "Type", "Level", "Message"]
    df["Level"]  = pd.to_numeric(df["Level"], errors="coerce").map(lambda x: f"{float(x):.2f}" if pd.notna(x) else "—")
    df["Symbol"] = df["Symbol"].astype(str).str.upper()
    df["Type"]   = df["Type"].astype(str).str.replace("_", " ", regex=False).str.title()
    _msg = df["Message"].astype(str)
    df["Message"] = _msg.where(_msg.str.len() <= 80, _msg.str.slice(0, 77) + "...")
    with st.expander("Alert history", expanded=False):
        st.caption("Newest first — up to 5 rows.")
        st.dataframe(df, use_container_width=True, hide_index=True, height=220)

def _render_how_we_got_here(result, split) -> None:
    if not result.feature_enabled:
        with st.expander("How we got there", expanded=False):
            st.caption("Buying ladder is disabled.")
        return

    items = []

    if result.drawdown_pct is not None:
        items.append((
            "blue",
            f"<strong>{html.escape(result.benchmark_symbol)}</strong> drawdown: "
            f"<strong>{result.drawdown_pct:.2f}%</strong> from ATH."
        ))

    items.append((
        "green",
        f"Active step <strong>{html.escape(result.ladder_step_label)}</strong> "
        f"(≤ {result.ladder_threshold_pct:.0f}% band, <strong>{result.multiplier:.2f}x</strong>)."
    ))

    items.append((
        "slate",
        f"Phase <strong>{html.escape(result.phase_label)}</strong>: base "
        f"<strong>{result.base_monthly:,.2f} €/mo</strong> → "
        f"<strong>{result.recommended_monthly:,.2f} €</strong> recommended "
        f"(<strong>{result.extra_vs_base:+,.2f} €</strong> vs base)."
    ))

    if split is not None and split.show_ui_block:
        extra_lines = getattr(split, "explanation_lines", ()) or ()
        for line in extra_lines[:1]:
            items.append(("purple", html.escape(str(line))))

    with st.expander("How we got there", expanded=False):
        body = ['<div class="how-body"><div class="how-list">']
        for kind, text in items:
            if kind == "blue":
                dot = '<span class="how-dot how-dot-blue"></span>'
            elif kind == "green":
                dot = '<span class="how-dot how-dot-green"></span>'
            elif kind == "purple":
                dot = '<span class="how-dot how-dot-purple"></span>'
            else:
                dot = '<span class="how-dot how-dot-slate">◴</span>'

            body.append(
                f'<div class="how-item">{dot}<div class="how-text">{text}</div></div>'
            )
        body.append('</div></div>')
        st.markdown("".join(body), unsafe_allow_html=True)
        
# ── Main entry point ──────────────────────────────────────────────────────────

def render_dashboard() -> None:
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)
    render_buying_ladder_sidebar()

    market_df, market_messages = build_market_overview(period=DEFAULT_LOOKBACK_PERIOD)

    render_header(market_df)
    _evaluate_alerts_safely(market_df=market_df, portfolio_value=_ibkr_total_value_for_alerts())

    # 1. Hero
    _render_investment_hero(market_df)

    # 2. Alerts
    render_alerts_section()

    st.markdown('<div class="ft-divider"></div>', unsafe_allow_html=True)

    # 3. Portfolio overview
    render_portfolio_overview()

    # 4. Market snapshot
    #st.markdown('<div class="ft-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Market snapshot</div>', unsafe_allow_html=True)
    render_warning_messages(market_messages)
    display_df = market_df.copy()
    if not display_df.empty:
        for col in ["Price", "Daily Change %", "Drawdown from ATH %"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].apply(lambda v: None if pd.isna(v) else v)
    if display_df.empty:
        st.info("Market data is not available right now.")
    else:
        primary, rest = _split_market_display_df(display_df)
        if not primary.empty:
            _render_market_cards_fintech(primary)
        if not rest.empty:
            with st.expander("More markets", expanded=False):
                _render_market_cards_fintech(rest)
    #st.markdown('</div>', unsafe_allow_html=True)

    # 5. Plan details
    _render_plan_details(market_df)

    st.markdown('<div class="ft-divider"></div>', unsafe_allow_html=True)

    # 6. Portfolio performance chart
    st.markdown('<div class="ft-card">', unsafe_allow_html=True)
    render_portfolio_performance_section()
    st.markdown('</div>', unsafe_allow_html=True)

    # 7. Alert history
    render_alert_history_section()