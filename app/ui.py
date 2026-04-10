"""Main UI rendering functions for the dashboard."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st

from alerts import AlertEngine, get_alert_settings
from app.components import format_percent, format_price, render_info_banner, render_warning_messages
from config import CHART_PERIOD_OPTIONS, DEFAULT_CHART_PERIOD, DEFAULT_LOOKBACK_PERIOD, TRACKED_INSTRUMENTS
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
        use_container_width=True,
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
        use_container_width=True,
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
        use_container_width=True,
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
    st.plotly_chart(fig, use_container_width=True)


def render_dashboard() -> None:
    """Render the complete dashboard page."""
    render_header()
    market_df = render_market_overview()
    st.divider()
    portfolio_total_value = render_portfolio_section()
    _evaluate_alerts_safely(market_df=market_df, portfolio_value=portfolio_total_value)
    st.divider()
    render_alerts_section()
    st.divider()
    render_chart_section()
