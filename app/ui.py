"""Main UI rendering functions for the dashboard."""
from __future__ import annotations



import html
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st
#st.write("APP RUNNING")

from alerts import AlertEngine, get_alert_settings
from app.components import format_percent, format_price, render_info_banner, render_warning_messages
from buying_ladder.ui import render_buying_ladder_card, render_buying_ladder_sidebar
from buying_ladder.logic import compute_buying_ladder
from buying_ladder.models import merge_with_defaults
from buying_ladder.storage import load_buying_ladder_settings
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


def _action_line_from_drawdown(drawdown_pct: Optional[float]) -> str:
    """Single confident action line for hero."""
    if drawdown_pct is None:
        return "Keep investing"
    if drawdown_pct <= -20:
        return "📉 Strong buying opportunity — deep drawdown"
    if drawdown_pct <= -10:
        return "📉 Increase exposure — market dip"
    return "Keep investing"


def _render_investment_hero(market_df: pd.DataFrame) -> None:
    """Top decision-first hero section."""
    st.subheader("This month")
    settings = merge_with_defaults(load_buying_ladder_settings())
    result = compute_buying_ladder(settings, market_df)

    recommended = result.recommended_monthly if result.feature_enabled else 0.0
    action_line = _action_line_from_drawdown(result.drawdown_pct if result.feature_enabled else None)
    action_safe = html.escape(action_line)

    with st.container(border=True):
        st.markdown(
            f"""
            <div style="margin-bottom: 1.35rem;">
              <div style="font-size: 2.35rem; font-weight: 700; line-height: 1.1; letter-spacing: -0.03em;">
                {recommended:,.0f} €
              </div>
              <div style="font-size: 0.85rem; opacity: 0.72; margin-top: 0.45rem;">
                Invest this month
              </div>
            </div>
            <div style="margin-bottom: 1.5rem;">
              <span style="font-size: 1.1rem; font-weight: 600;">{action_safe}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Guidance only — not investment advice.")
        st.markdown("")


_PRIMARY_MARKET_SYMBOLS = frozenset({"VWCE", "CNDX"})

# Short human descriptions for market cards (UI only).
_SYMBOL_DESCRIPTIONS: Dict[str, str] = {
    "VWCE": "Global stock market ETF (all-world exposure)",
    "CNDX": "Nasdaq 100 (tech-focused companies)",
    "SPY": "S&P 500 (largest US companies)",
    "VIX": "Volatility index (market fear gauge)",
    "DXY": "US Dollar strength index",
    "TNX": "10-year US Treasury yield",
}


def _split_market_display_df(display_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Primary row(s) for main view vs the rest for 'More markets'."""
    if display_df.empty or "Symbol" not in display_df.columns:
        return display_df, pd.DataFrame()
    sym = display_df["Symbol"].astype(str).str.upper().str.strip()
    primary = display_df[sym.isin(_PRIMARY_MARKET_SYMBOLS)].copy()
    rest = display_df[~sym.isin(_PRIMARY_MARKET_SYMBOLS)].copy()
    return primary, rest


def _render_market_cards(display_df: pd.DataFrame) -> None:
    """Stacked instrument cards; daily change uses metric delta for green/red semantics."""
    if display_df.empty:
        st.info("Market data is not available right now. Check back in a moment.")
        return
    fields = ["Symbol", "Name", "Ticker", "Price", "Daily Change %", "Drawdown from ATH %"]
    for _, row in display_df[fields].iterrows():
        with st.container(border=True):
            title = str(row.get("Symbol") or row.get("Ticker") or "Instrument")
            st.markdown(f"**{title}**")
            sym_key = title.strip().upper()
            if sym_key in _SYMBOL_DESCRIPTIONS:
                st.caption(_SYMBOL_DESCRIPTIONS[sym_key])
            daily_raw = row.get("Daily Change %")
            delta_val: Optional[float] = None
            if daily_raw is not None and not pd.isna(daily_raw):
                try:
                    delta_val = float(daily_raw)
                except (TypeError, ValueError):
                    delta_val = None
            st.caption("Last price")
            if delta_val is not None:
                st.metric(" ", format_price(row.get("Price")), delta=delta_val)
            else:
                st.metric(" ", format_price(row.get("Price")))
            st.caption("Down from peak")
            st.markdown(f"**{format_percent(row.get('Drawdown from ATH %'))}**")


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
    """Neutral data freshness line (no technical source jargon)."""
    st.caption("Using last available data")


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
    alerts_history = st.session_state.get("alerts", [])

    if not alerts_history:
        st.subheader("All clear")
        st.info("Nothing needs your attention")
        return

    st.subheader("Needs attention")
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    sorted_alerts = sorted(
        alerts_history,
        key=lambda a: (
            severity_rank.get(str(a.get("severity", "")).lower(), 0),
            str(a.get("timestamp", "")),
        ),
        reverse=True,
    )

    top_alert = sorted_alerts[0]
    style, _ = _severity_style(top_alert)
    actionable_text = top_alert.get("message", "Alert")
    if style == "error":
        st.error(actionable_text)
    elif style == "warning":
        st.warning(actionable_text)
    else:
        st.info(actionable_text)

    if len(sorted_alerts) > 1:
        with st.expander("Other alerts", expanded=False):
            for alert in sorted_alerts[1:5]:
                detail = f"{alert.get('message', 'Alert')} ({alert.get('timestamp', '')})"
                style, _ = _severity_style(alert)
                if style == "error":
                    st.error(detail)
                elif style == "warning":
                    st.warning(detail)
                else:
                    st.info(detail)


def render_header() -> None:
    st.title("Personal Investment Dashboard")
    st.caption("Decide what to invest, spot issues early, see where you stand.")
    render_info_banner()


def render_market_overview() -> pd.DataFrame:
    """Render market overview as mobile-friendly instrument cards."""
    st.subheader("Market snapshot")
    overview_df, messages = build_market_overview(period=DEFAULT_LOOKBACK_PERIOD)
    render_warning_messages(messages)

    display_df = overview_df.copy()
    if not display_df.empty:
        for col in ["Price", "Daily Change %", "Drawdown from ATH %"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].apply(lambda v: None if pd.isna(v) else v)
    if display_df.empty:
        st.info("Market data is not available right now. Check back in a moment.")
        return overview_df

    primary, rest = _split_market_display_df(display_df)
    if primary.empty:
        if rest.empty:
            st.info("Market data is not available right now. Check back in a moment.")
        else:
            st.caption("VWCE & CNDX will show here when available.")
    else:
        _render_market_cards(primary)
    if not rest.empty:
        with st.expander("More markets", expanded=False):
            _render_market_cards(rest)
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
    st.subheader("Portfolio")
    st.caption("Edits here apply for this session only.")
    if not st.session_state.get("_portfolio_has_holdings", False):
        st.info("No holdings yet — add your first position below.")
    portfolio_input = _portfolio_input_table()
    cleaned_portfolio = _validate_portfolio_rows(portfolio_input)

    if cleaned_portfolio.empty:
        st.session_state["_portfolio_has_holdings"] = False
        return None
    st.session_state["_portfolio_has_holdings"] = True

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
        st.warning(f"No live price for: {tickers_text}. Check the ticker or try again later.")

    result_df = pd.DataFrame(portfolio_rows)

    totals_df = result_df.dropna(subset=["Cost Basis", "Current Value", "Unrealized PnL"])
    if totals_df.empty:
        with st.expander("Holdings", expanded=False):
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
        return None

    total_cost_basis = totals_df["Cost Basis"].sum()
    total_current_value = totals_df["Current Value"].sum()
    total_pnl = totals_df["Unrealized PnL"].sum()
    total_pnl_pct = calculate_unrealized_pnl_percent(total_pnl, total_cost_basis)

    st.markdown("")
    c1, c2 = st.columns(2)
    c1.metric("Portfolio value", format_price(total_current_value), delta=float(total_pnl))
    c2.metric("Total return", format_percent(total_pnl_pct))
    st.markdown("")
    c3, c4 = st.columns(2)
    c3.metric("Cost basis", format_price(total_cost_basis))
    c4.metric("Unrealized gain / loss", format_price(total_pnl))

    with st.expander("Holdings", expanded=False):
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
    return total_current_value


def render_portfolio_performance_section() -> None:
    """Show persisted portfolio history from SQLite (worker) with value and growth charts."""
    st.divider()
    st.subheader("📈 Portfolio over time")
    _render_portfolio_source_indicator()
    days = st.selectbox("Period", [7, 30, 90], index=1, key="portfolio_performance_period")

    df = get_portfolio_history(days=days)
    if df.empty:
        st.info("No history yet — data appears after your portfolio syncs.")
        return

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["total_value"] = pd.to_numeric(df["total_value"], errors="coerce")
    for col in ("vwce_value", "cndx_value", "cash"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["timestamp", "total_value"])
    if df.empty:
        st.info("No history yet — data appears after your portfolio syncs.")
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
        st.info("No history yet — data appears after your portfolio syncs.")
        return

    last_value = float(chart_df["total_value"].iloc[-1])
    last_growth = float(chart_df["growth_pct"].iloc[-1])

    col1, col2 = st.columns(2)
    col1.metric("Portfolio value", f"{last_value:,.0f} €".replace(",", " "))
    col2.metric("Period return", f"{last_growth:.2f} %", delta=last_growth)

    st.markdown("")

    if len(chart_df) < 2:
        st.warning("Need more history to draw a useful chart.")
        return

    with st.container():
        st.caption("Value (€)")
        st.line_chart(chart_df[["total_value"]])
        st.caption("Return in this period (%)")
        st.line_chart(chart_df[["growth_pct"]])


def render_alert_history_section() -> None:
    """Show recent alerts persisted in SQLite (worker)."""
    st.divider()

    df = get_recent_alerts(limit=5)
    if df.empty:
        st.info("No saved alerts yet.")
        return

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        st.info("Nothing to show here.")
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

    with st.expander("Alert history", expanded=False):
        st.caption("Newest first — up to 5 rows.")
        st.dataframe(df, width="stretch", hide_index=True, height=220)


def render_chart_section() -> None:
    """Render instrument price chart with selectable time range."""
    st.subheader("Price chart")
    st.caption("Choose what to look at.")
    instrument_options = list(TRACKED_INSTRUMENTS.keys())
    selected_symbol = st.selectbox("What to view", options=instrument_options, index=0)
    selected_period = st.selectbox(
        "Range",
        options=CHART_PERIOD_OPTIONS,
        index=CHART_PERIOD_OPTIONS.index(DEFAULT_CHART_PERIOD),
    )

    selected_ticker = TRACKED_INSTRUMENTS[selected_symbol]["ticker"]
    history = fetch_history_for_ticker(ticker=selected_ticker, period=selected_period)
    chart_df = normalize_history_for_chart(history)
    if chart_df is None:
        logger.info("Chart unavailable for symbol=%s ticker=%s", selected_symbol, selected_ticker)
        st.info(f"No price history for {selected_symbol} right now — try another range or symbol.")
        return

    fig = px.line(
        chart_df,
        x="Date",
        y="Close",
        title=f"{selected_symbol} - Close Price ({selected_period})",
        labels={"Close": "Price", "Date": "Date"},
    )
    fig.update_layout(margin={"l": 10, "r": 10, "t": 50, "b": 10}, height=420)
    st.plotly_chart(
        fig,
        width="stretch",
        config={"displayModeBar": False},
    )


def render_dashboard() -> None:
    """Render the complete dashboard page."""
    render_buying_ladder_sidebar()
    render_header()

    market_df, market_messages = build_market_overview(period=DEFAULT_LOOKBACK_PERIOD)

    # 1) Investment decision (hero)
    with st.container():
        _render_investment_hero(market_df)

    # Refresh market-based alerts before rendering the alert section.
    _evaluate_alerts_safely(market_df=market_df, portfolio_value=None)

    st.divider()

    # 2) Alerts (right after hero)
    with st.container():
        render_alerts_section()

    st.divider()

    # 3) Portfolio
    with st.container():
        portfolio_total_value = render_portfolio_section()

    _evaluate_alerts_safely(market_df=market_df, portfolio_value=portfolio_total_value)

    st.divider()

    # 4) Market data (de-emphasized)
    with st.container():
        st.subheader("Market snapshot")
        st.caption("Context for prices and drawdowns.")
        render_warning_messages(market_messages)
        display_df = market_df.copy()
        if not display_df.empty:
            for col in ["Price", "Daily Change %", "Drawdown from ATH %"]:
                if col in display_df.columns:
                    display_df[col] = display_df[col].apply(lambda v: None if pd.isna(v) else v)
        if display_df.empty:
            st.info("Market data is not available right now. Check back in a moment.")
        else:
            primary, rest = _split_market_display_df(display_df)
            if primary.empty:
                if rest.empty:
                    st.info("Market data is not available right now. Check back in a moment.")
                else:
                    st.caption("VWCE & CNDX will show here when available.")
            else:
                _render_market_cards(primary)
            if not rest.empty:
                with st.expander("More markets", expanded=False):
                    _render_market_cards(rest)

    st.divider()

    # Secondary info and details
    with st.container():
        with st.expander("Details", expanded=False):
            render_buying_ladder_card(market_df)
            render_chart_section()
            render_portfolio_performance_section()
            render_alert_history_section()
