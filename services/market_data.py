"""Market data service layer using yfinance."""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf

from config import CACHE_TTL_SECONDS, DEFAULT_LOOKBACK_PERIOD, TRACKED_INSTRUMENTS
from logic.calculations import (
    calculate_all_time_high,
    calculate_daily_percent_change,
    calculate_drawdown_from_ath,
    safe_float,
)

logger = logging.getLogger(__name__)
YFINANCE_TIMEOUT_SECONDS = 15


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_history_for_ticker(ticker: str, period: str = DEFAULT_LOOKBACK_PERIOD) -> pd.DataFrame:
    """Fetch historical OHLCV data for a single ticker (Streamlit-cached)."""
    return _download_history(ticker=ticker, period=period)


def fetch_history_for_ticker_uncached(ticker: str, period: str = DEFAULT_LOOKBACK_PERIOD) -> pd.DataFrame:
    """Fetch historical OHLCV data for a single ticker (non-Streamlit execution)."""
    return _download_history(ticker=ticker, period=period)


def _download_history(ticker: str, period: str) -> pd.DataFrame:
    """Shared yfinance download implementation."""
    try:
        data = yf.download(
            tickers=ticker,
            period=period,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=YFINANCE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("Failed to fetch history for ticker=%s period=%s: %s", ticker, period, exc)
        return pd.DataFrame()

    if data is None or data.empty:
        logger.info("Empty history returned for ticker=%s period=%s", ticker, period)
        return pd.DataFrame()
    return data


def _download_histories_batch(tickers: List[str], period: str) -> pd.DataFrame:
    """Fetch historical data for multiple tickers in one request."""
    if not tickers:
        return pd.DataFrame()
    try:
        data = yf.download(
            tickers=tickers,
            period=period,
            auto_adjust=False,
            progress=False,
            threads=False,
            group_by="column",
            timeout=YFINANCE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("Failed to fetch batch history for tickers=%s period=%s: %s", tickers, period, exc)
        return pd.DataFrame()

    if data is None or data.empty:
        logger.info("Empty batch history returned for period=%s", period)
        return pd.DataFrame()
    return data


def _extract_close_from_batch(data: pd.DataFrame, ticker: str) -> Optional[pd.Series]:
    """Extract close series for a ticker from a batch yfinance dataframe."""
    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        # Typical batch shape: level0 OHLCV, level1 ticker symbols
        if "Close" in data.columns.get_level_values(0):
            close_frame = data["Close"]
            if isinstance(close_frame, pd.Series):
                return pd.to_numeric(close_frame, errors="coerce").dropna()
            if ticker in close_frame.columns:
                close_series = pd.to_numeric(close_frame[ticker], errors="coerce").dropna()
                return None if close_series.empty else close_series
        return None

    # Single ticker shaped response fallback
    return _extract_close_series(data)


def _extract_close_series(data: pd.DataFrame) -> Optional[pd.Series]:
    """Extract a clean 1D close-price series from yfinance data."""
    if data is None or data.empty:
        return None

    close_obj: Optional[pd.Series | pd.DataFrame] = None

    # Standard single-level columns (Open, High, Low, Close, ...)
    if "Close" in data.columns:
        close_obj = data["Close"]
    # MultiIndex columns from yfinance (e.g. ('Close', 'SPY'))
    elif isinstance(data.columns, pd.MultiIndex):
        close_cols = [col for col in data.columns if str(col[0]).lower() == "close"]
        if close_cols:
            close_obj = data.loc[:, close_cols]

    if close_obj is None:
        return None

    # yfinance may return Close as a 1-col DataFrame; normalize to Series.
    if isinstance(close_obj, pd.DataFrame):
        if close_obj.empty:
            return None
        close_series = close_obj.iloc[:, 0]
    else:
        close_series = close_obj

    close_series = pd.to_numeric(close_series, errors="coerce").dropna()
    if close_series.empty:
        return None
    return close_series


def normalize_history_for_chart(data: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    Normalize yfinance history into `Date` and `Close` columns for charting.

    Returns None when required columns are unavailable or close values are all invalid.
    """
    close_series = _extract_close_series(data)
    if close_series is None:
        return None

    chart_df = close_series.rename("Close").reset_index()
    if chart_df.empty:
        return None

    if "Date" not in chart_df.columns:
        first_col = chart_df.columns[0]
        chart_df = chart_df.rename(columns={first_col: "Date"})

    chart_df["Close"] = pd.to_numeric(chart_df["Close"], errors="coerce")
    chart_df = chart_df.dropna(subset=["Close"])
    if chart_df.empty:
        return None
    return chart_df


def build_market_overview(
    period: str = DEFAULT_LOOKBACK_PERIOD,
    history_fetcher: Callable[[str, str], pd.DataFrame] = fetch_history_for_ticker,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Build market overview table with latest price, daily change, and drawdown.

    Returns:
        - DataFrame with overview rows
        - List of warnings/errors encountered per ticker
    """
    rows: List[Dict[str, object]] = []
    messages: List[str] = []
    ticker_list = [meta["ticker"] for meta in TRACKED_INSTRUMENTS.values()]

    close_cache: Dict[str, Optional[pd.Series]] = {}
    if history_fetcher in {fetch_history_for_ticker, fetch_history_for_ticker_uncached}:
        batch_data = _download_histories_batch(tickers=ticker_list, period=period)
        if not batch_data.empty:
            for ticker in ticker_list:
                close_cache[ticker] = _extract_close_from_batch(batch_data, ticker)

    for symbol, meta in TRACKED_INSTRUMENTS.items():
        ticker = meta["ticker"]
        name = meta["name"]

        close_series = close_cache.get(ticker)
        if close_series is None:
            history = history_fetcher(ticker=ticker, period=period)
            close_series = _extract_close_series(history)

        if close_series is None:
            logger.info("Skipping market row for symbol=%s ticker=%s due to missing close data", symbol, ticker)
            messages.append(f"Could not load data for {symbol} ({ticker}).")
            rows.append(
                {
                    "Symbol": symbol,
                    "Name": name,
                    "Ticker": ticker,
                    "Price": None,
                    "Daily Change %": None,
                    "Drawdown from ATH %": None,
                }
            )
            continue

        latest_price = safe_float(close_series.iloc[-1])
        daily_change = calculate_daily_percent_change(close_series)
        drawdown = calculate_drawdown_from_ath(close_series)

        rows.append(
            {
                "Symbol": symbol,
                "Name": name,
                "Ticker": ticker,
                "Price": latest_price,
                "Daily Change %": daily_change,
                "Drawdown from ATH %": drawdown,
                "ATH": calculate_all_time_high(close_series),
            }
        )

    overview_df = pd.DataFrame(rows)
    return overview_df, messages


def get_latest_price_map(tickers: List[str], period: str = "1mo") -> Dict[str, Optional[float]]:
    """Return mapping of ticker -> latest close price."""
    price_map: Dict[str, Optional[float]] = {}

    # Preserve order while removing duplicates to avoid redundant API calls.
    unique_tickers = list(dict.fromkeys(tickers))
    for ticker in unique_tickers:
        history = fetch_history_for_ticker(ticker=ticker, period=period)
        close_series = _extract_close_series(history)
        if close_series is None:
            logger.info("No latest price available for ticker=%s period=%s", ticker, period)
            price_map[ticker] = None
            continue
        price_map[ticker] = safe_float(close_series.iloc[-1])

    return price_map
