"""Application configuration for the investment dashboard MVP."""

from __future__ import annotations


TRACKED_INSTRUMENTS = {
    "VWCE": {"ticker": "VWCE.DE", "name": "Vanguard FTSE All-World UCITS ETF"},
    "CNDX": {"ticker": "CNDX.AS", "name": "iShares Nasdaq 100 UCITS ETF"},
    "SPY": {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust"},
    "QQQ": {"ticker": "QQQ", "name": "Invesco QQQ Trust"},
    "VIX": {"ticker": "^VIX", "name": "CBOE Volatility Index"},
    "DXY": {"ticker": "DX-Y.NYB", "name": "US Dollar Index"},
    "TNX": {"ticker": "^TNX", "name": "US 10-Year Treasury Yield"},
}

# Default market data windows
DEFAULT_LOOKBACK_PERIOD = "1y"
DEFAULT_CHART_PERIOD = "6mo"

# Cache/refresh settings
CACHE_TTL_SECONDS = 300

# Available quick-select chart periods
CHART_PERIOD_OPTIONS = ["1mo", "3mo", "6mo", "1y", "2y", "5y"]

DRAW_DOWN_ALERT_SYMBOLS = ["VWCE", "CNDX", "SPY", "QQQ"]

drawdown_levels = (0,)