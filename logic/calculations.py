"""Reusable calculation helpers for market and portfolio metrics."""

from __future__ import annotations

from typing import Optional

import pandas as pd


def safe_float(value: object) -> Optional[float]:
    """Convert values to float safely, returning None on failure."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_daily_percent_change(close_series: pd.Series) -> Optional[float]:
    """Return day-over-day percent change from the last two close values."""
    if close_series is None or close_series.empty or len(close_series) < 2:
        return None

    latest = safe_float(close_series.iloc[-1])
    previous = safe_float(close_series.iloc[-2])
    if latest is None or previous in (None, 0):
        return None

    return ((latest - previous) / previous) * 100.0


def calculate_all_time_high(close_series: pd.Series) -> Optional[float]:
    """Return all-time high close from a price series."""
    if close_series is None or close_series.empty:
        return None

    ath = safe_float(close_series.max())
    return ath


def calculate_drawdown_from_ath(close_series: pd.Series) -> Optional[float]:
    """Return current drawdown percent from ATH (negative or zero)."""
    if close_series is None or close_series.empty:
        return None

    latest = safe_float(close_series.iloc[-1])
    ath = calculate_all_time_high(close_series)
    if latest is None or ath in (None, 0):
        return None

    return ((latest - ath) / ath) * 100.0


def calculate_market_value(quantity: Optional[float], current_price: Optional[float]) -> Optional[float]:
    """Position market value = quantity * current price."""
    qty = safe_float(quantity)
    px = safe_float(current_price)
    if qty is None or px is None:
        return None
    return qty * px


def calculate_cost_basis(quantity: Optional[float], avg_buy_price: Optional[float]) -> Optional[float]:
    """Position cost basis = quantity * average buy price."""
    qty = safe_float(quantity)
    avg = safe_float(avg_buy_price)
    if qty is None or avg is None:
        return None
    return qty * avg


def calculate_unrealized_pnl(market_value: Optional[float], cost_basis: Optional[float]) -> Optional[float]:
    """Unrealized PnL = market value - cost basis."""
    mv = safe_float(market_value)
    cb = safe_float(cost_basis)
    if mv is None or cb is None:
        return None
    return mv - cb


def calculate_unrealized_pnl_percent(
    unrealized_pnl: Optional[float], cost_basis: Optional[float]
) -> Optional[float]:
    """Unrealized PnL percentage relative to cost basis."""
    pnl = safe_float(unrealized_pnl)
    cb = safe_float(cost_basis)
    if pnl is None or cb in (None, 0):
        return None
    return (pnl / cb) * 100.0
