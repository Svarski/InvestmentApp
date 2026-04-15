"""Main UI rendering functions for the dashboard."""
from __future__ import annotations

import streamlit as st
st.write("APP RUNNING")

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px


from alerts import AlertEngine, get_alert_settings
from app.components import format_percent, format_price, render_info_banner, render_warning_messages
from buying_ladder.ui import render_buying_ladder_card, render_buying_ladder_sidebar
from config import CHART_PERIOD_OPTIONS, DEFAULT_CHART_PERIOD, DEFAULT_LOOKBACK_PERIOD, TRACKED_INSTRUMENTS
from db import get_portfolio_history, get_recent_alerts
from logic.calculations import (
    calculate_cost_basis,
    calculate_market_value,
    calculate_unrealized_pnl,
    calculate_unrealized_pnl_percent,
)
from services.market_data import (
    build_market_overview,
    fetch_history_for_ticker,
    get_latest_price_map,
    normalize_history_for_chart,
)

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT_MAX_AGE_SEC = 300


def _parse_last_success_timestamp_utc(value: object) -> Optional[datetime]:
    """Parse worker heartbeat ``last_success_timestamp`` (ISO) to aware UTC datetime."""
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
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _heartbeat_success_is_stale(raw: dict) -> bool:
    """True if last_success_timestamp is missing, invalid, or older than WORKER_HEARTBEAT_MAX_AGE_SEC."""
    parsed = _parse_last_success_timestamp_utc(raw.get("last_success_timestamp"))
    if parsed is None:
        return True
    age_sec = (datetime.now(timezone.utc) - parsed).total_seconds()
    return age_sec > WORKER_HEARTBEAT_MAX_AGE_SEC


def _read_worker_heartbeat_state() -> Tuple[bool, str, Optional[str]]:
    """Returns (worker_heartbeat_stale, portfolio_source, last_ibkr_caption).

    If WORKER_HEARTBEAT_FILE is unset, heartbeat is not treated as stale (unknown).
    """
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
            last_ibkr_line = None

    return heartbeat_stale, src, last_ibkr_line


def _render_portfolio_source_indicator() -> None:
    """Worker portfolio source and heartbeat freshness (last successful worker cycle)."""
    heartbeat_stale, src, last_ibkr = _read_worker_heartbeat_state()
    if heartbeat_stale:
        st.error("Worker not running or heartbeat stale")
    if src == "IBKR":
        st.success("Portfolio source: IBKR")
    elif src == "IBKR_STALE":
        st.warning("Portfolio source: IBKR (stale)")
    else:
        st.error("Portfolio source: Fallback")
    if last_ibkr:
        st.caption(f"Last IBKR update: {last_ibkr}")


def _get_alert_engine() -> AlertEngine:
    """Return a single session-scoped alert engine instance."""
    if "alert_engine" not in st.session_state:
        st.session_state["alert_engine"] = AlertEngine(settings=get_alert_settings())
    return st.session_state["alert_engine"]


def _append_alert_history(new_alerts: list, max_items: int = 50) -> None:
    """Append unique alerts to history and keep newest `max_items` entries."""
    if "alerts" not in st.session_state:
        st.session_state["alerts"] = []

    history = st.session_state["alerts"]
    existing_keys = {(item.get("id"), item.get("timestamp")) for item in history}

    for alert in new_alerts:
        key = (alert.id, alert.timestamp)
        if key in existing_keys:
            continue
        history.append(
            {
                "id": alert.id,
                "type": alert.type,
                "message": alert.message,
                "severity": alert.severity,
                "timestamp": alert.timestamp,
            }
        )
        existing_keys.add(key)

    st.session_state["alerts"] = history[-max_items:]


def _evaluate_alerts_safely(market_df: pd.DataFrame, portfolio_value: Optional[float]) -> None:
    """Evaluate alert engine using current dashboard data without crashing UI."""
    required_columns = {"Symbol", "Drawdown from ATH %", "Price"}
    if market_df is None or market_df.empty or not required_columns.issubset(set(market_df.columns)):
        logger.info("Skipping alert evaluation due to missing market columns.")
        return

    try:
        engine = _get_alert_engine()
        new_alerts = engine.evaluate(market_df=market_df, portfolio_value=portfolio_value)
        _append_alert_history(new_alerts=new_alerts, max_items=50)
    except Exception:
        logger.exception("Alert evaluation failed.")


def _severity_style(alert: Dict[str, str]) -> Tuple[str, str]:
    """Map severity into Streamlit rendering function and label."""
    severity = str(alert.get("severity", "")).lower()
    if severity == "high":
        return "error", "HIGH"
    if severity == "medium":
        return "warning", "MEDIUM"
    return "info", "LOW"


def render_alerts_section() -> None:
    """Render recent alerts from session history."""
    st.subheader("Alerts")
    alerts_history = st.session_state.get("alerts", [])

    if not alerts_history:
        st.info("No active alerts")
        return

    with st.expander("Recent alerts", expanded=True):
        for alert in reversed(alerts_history):
            style, severity_label = _severity_style(alert)
            alert_text = (
                f"[{severity_label}] {alert.get('message', 'Alert')}\n"
                f"{alert.get('timestamp', '')} | {alert.get('type', 'unknown')}"
            )
            if style == "error":
                st.error(alert_text)
            elif style == "warning":
                st.warning(alert_text)
            else:
                st.info(alert_text)


def render_header() -> None:
    st.title("Personal Investment Dashboard")
    st.caption("MVP - Market overview, manual portfolio tracking, and simple charts")
    render_info_banner()


def render_market_overview() -> pd.DataFrame:
    """Render market overview table using configured tracked instruments."""
    st.subheader("Market Overview")
    overview_df, messages = build_market_overview(period=DEFAULT_LOOKBACK_PERIOD)
    render_warning_messages(messages)

    display_df = overview_df.copy()
    if not display_df.empty:
        for col in ["Price", "Daily Change %", "Drawdown from ATH %"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].apply(lambda v: None if pd.isna(v) else v)

    st.dataframe(
        display_df[["Symbol", "Name", "Ticker", "Price", "Daily Change %", "Drawdown from ATH %"]],
        width="stretch",
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn("Price", format="%.2f"),
            "Daily Change %": st.column_config.NumberColumn("Daily Change %", format="%.2f%%"),
            "Drawdown from ATH %": st.column_config.NumberColumn("Drawdown from ATH %", format="%.2f%%"),
        },
    )
    return overview_df


def _portfolio_input_table(default_rows: int = 3) -> pd.DataFrame:
    """Render editable portfolio input grid."""
    initial_df = pd.DataFrame(
        {
            "Ticker": [""] * default_rows,
            "Quantity": [0.0] * default_rows,
            "Avg Buy Price": [0.0] * default_rows,
        }
    )
    return st.data_editor(
        initial_df,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "Ticker": st.column_config.TextColumn("Ticker", help="Example: SPY"),
            "Quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0),
            "Avg Buy Price": st.column_config.NumberColumn("Avg Buy Price", min_value=0.0, step=0.01),
        },
    )


def _validate_portfolio_rows(portfolio_df: pd.DataFrame) -> pd.DataFrame:
    """Keep rows with valid ticker, quantity, and average buy price."""
    cleaned = portfolio_df.copy()
    cleaned["Ticker"] = cleaned["Ticker"].astype(str).str.strip().str.upper()
    cleaned = cleaned[cleaned["Ticker"] != ""]
    cleaned = cleaned[(cleaned["Quantity"] > 0) & (cleaned["Avg Buy Price"] > 0)]
    return cleaned


def render_portfolio_section() -> Optional[float]:
    """Render manual portfolio editor and computed portfolio metrics."""
    st.subheader("Manual Portfolio")
    st.caption("Enter holdings manually. Data is in-memory for this MVP session only.")
    portfolio_input = _portfolio_input_table()
    cleaned_portfolio = _validate_portfolio_rows(portfolio_input)

    if cleaned_portfolio.empty:
        st.info("Add at least one valid row with ticker, quantity, and average buy price.")
        return None

    unique_tickers = cleaned_portfolio["Ticker"].dropna().unique().tolist()
    latest_prices = get_latest_price_map(unique_tickers)

    portfolio_rows: List[Dict[str, object]] = []
    missing_tickers: List[str] = []

    for _, row in cleaned_portfolio.iterrows():
        ticker = row["Ticker"]
        quantity = float(row["Quantity"])
        avg_buy_price = float(row["Avg Buy Price"])
        current_price = latest_prices.get(ticker)

        if current_price is None:
            logger.info("Portfolio ticker has no market price: %s", ticker)
            missing_tickers.append(ticker)
            portfolio_rows.append(
                {
                    "Ticker": ticker,
                    "Quantity": quantity,
                    "Avg Buy Price": avg_buy_price,
                    "Current Price": None,
                    "Cost Basis": calculate_cost_basis(quantity, avg_buy_price),
                    "Current Value": None,
                    "Unrealized PnL": None,
                    "Unrealized PnL %": None,
                }
            )
            continue

        market_value = calculate_market_value(quantity, current_price)
        cost_basis = calculate_cost_basis(quantity, avg_buy_price)
        pnl = calculate_unrealized_pnl(market_value, cost_basis)
        pnl_pct = calculate_unrealized_pnl_percent(pnl, cost_basis)

        portfolio_rows.append(
            {
                "Ticker": ticker,
                "Quantity": quantity,
                "Avg Buy Price": avg_buy_price,
                "Current Price": current_price,
                "Cost Basis": cost_basis,
                "Current Value": market_value,
                "Unrealized PnL": pnl,
                "Unrealized PnL %": pnl_pct,
            }
        )

    if missing_tickers:
        tickers_text = ", ".join(sorted(set(missing_tickers)))
        st.warning(f"Could not load current prices for: {tickers_text}")

    result_df = pd.DataFrame(portfolio_rows)
    st.dataframe(
        result_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Quantity": st.column_config.NumberColumn("Quantity", format="%.4f"),
            "Avg Buy Price": st.column_config.NumberColumn("Avg Buy Price", format="%.2f"),
            "Current Price": st.column_config.NumberColumn("Current Price", format="%.2f"),
            "Cost Basis": st.column_config.NumberColumn("Cost Basis", format="%.2f"),
            "Current Value": st.column_config.NumberColumn("Current Value", format="%.2f"),
            "Unrealized PnL": st.column_config.NumberColumn("Unrealized PnL", format="%.2f"),
            "Unrealized PnL %": st.column_config.NumberColumn("Unrealized PnL %", format="%.2f%%"),
        },
    )

    totals_df = result_df.dropna(subset=["Cost Basis", "Current Value", "Unrealized PnL"])
    if totals_df.empty:
        return None

    total_cost_basis = totals_df["Cost Basis"].sum()
    total_current_value = totals_df["Current Value"].sum()
    total_pnl = totals_df["Unrealized PnL"].sum()
    total_pnl_pct = calculate_unrealized_pnl_percent(total_pnl, total_cost_basis)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Portfolio Value", format_price(total_current_value))
    col2.metric("Cost Basis", format_price(total_cost_basis))
    col3.metric("Unrealized PnL", format_price(total_pnl))
    col4.metric("Unrealized PnL %", format_percent(total_pnl_pct))
    return total_current_value


def render_portfolio_performance_section() -> None:
    """Show persisted portfolio history from SQLite (worker) with value and growth charts."""
    st.divider()
    st.subheader("📈 Portfolio Performance")
    _render_portfolio_source_indicator()
    days = st.selectbox("Period", [7, 30, 90], index=1, key="portfolio_performance_period")

    df = get_portfolio_history(days=days)
    if df.empty:
        st.info("No portfolio data yet")
        return

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce")
    for col in ("vwce_value", "cndx_value", "cash"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["timestamp", "total_value"])
    if df.empty:
        st.info("No portfolio data yet")
        return

    df = df.sort_values("timestamp")
    if len(df) == 1:
        df["growth_pct"] = 0.0
    else:
        baseline = df["total_value"].iloc[0]
        if pd.isna(baseline) or baseline == 0:
            df["growth_pct"] = 0.0
        else:
            df["growth_pct"] = (df["total_value"] / baseline - 1) * 100

    df = df.set_index("timestamp")
    chart_df = df[["total_value", "growth_pct"]].dropna(how="any")
    if chart_df.empty:
        st.info("No portfolio data yet")
        return

    last_value = float(chart_df["total_value"].iloc[-1])
    last_growth = float(chart_df["growth_pct"].iloc[-1])

    col1, col2 = st.columns(2)
    col1.metric("Portfolio Value", f"{last_value:,.0f} €".replace(",", " "))
    col2.metric("Growth", f"{last_growth:.2f} %", delta=last_growth)

    st.markdown("")

    if len(chart_df) < 2:
        st.warning("Not enough data for meaningful chart")
        return

    with st.container():
        st.caption("Portfolio value over time (€)")
        st.line_chart(chart_df[["total_value"]])
        st.caption("Growth (%)")
        st.line_chart(chart_df[["growth_pct"]])


def render_alert_history_section() -> None:
    """Show recent alerts persisted in SQLite (worker)."""
    st.divider()
    st.subheader("🚨 Alert History")

    df = get_recent_alerts(limit=20)
    if df.empty:
        st.info("No alerts yet")
        return

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        st.info("No valid alerts to display")
        return
    df = df.sort_values("timestamp", ascending=False, na_position="last")
    df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M")

    df = df[["timestamp", "symbol", "type", "level", "message"]]
    df.columns = ["Time", "Symbol", "Type", "Level", "Message"]

    df["Level"] = pd.to_numeric(df["Level"], errors="coerce")
    df["Level"] = df["Level"].map(lambda x: f"{float(x):.2f}" if pd.notna(x) else "—")
    df["Symbol"] = df["Symbol"].astype(str).str.upper()
    df["Type"] = df["Type"].astype(str).str.replace("_", " ", regex=False).str.title()

    _msg = df["Message"].astype(str)
    df["Message"] = _msg.where(_msg.str.len() <= 80, _msg.str.slice(0, 77) + "...")

    st.caption("Latest alerts (most recent first)")
    st.dataframe(df, width="stretch", hide_index=True, height=300)


def render_chart_section() -> None:
    """Render instrument price chart with selectable time range."""
    st.subheader("Price Chart")
    instrument_options = list(TRACKED_INSTRUMENTS.keys())
    selected_symbol = st.selectbox("Instrument", options=instrument_options, index=0)
    selected_period = st.selectbox(
        "Time range",
        options=CHART_PERIOD_OPTIONS,
        index=CHART_PERIOD_OPTIONS.index(DEFAULT_CHART_PERIOD),
    )

    selected_ticker = TRACKED_INSTRUMENTS[selected_symbol]["ticker"]
    history = fetch_history_for_ticker(ticker=selected_ticker, period=selected_period)
    chart_df = normalize_history_for_chart(history)
    if chart_df is None:
        logger.info("Chart unavailable for symbol=%s ticker=%s", selected_symbol, selected_ticker)
        st.info(f"No chart data available for {selected_symbol} ({selected_ticker}).")
        return

    fig = px.line(
        chart_df,
        x="Date",
        y="Close",
        title=f"{selected_symbol} - Close Price ({selected_period})",
        labels={"Close": "Price", "Date": "Date"},
    )
    fig.update_layout(margin={"l": 10, "r": 10, "t": 50, "b": 10}, height=420)
    st.plotly_chart(fig, width="stretch")


def render_dashboard() -> None:
    """Render the complete dashboard page."""
    render_buying_ladder_sidebar()
    render_header()
    market_df = render_market_overview()
    render_buying_ladder_card(market_df)
    st.divider()
    portfolio_total_value = render_portfolio_section()
    render_portfolio_performance_section()
    render_alert_history_section()
    _evaluate_alerts_safely(market_df=market_df, portfolio_value=portfolio_total_value)
    st.divider()
    render_alerts_section()
    st.divider()
    render_chart_section()
