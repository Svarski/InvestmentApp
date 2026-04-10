"""Pure alert rule checks (no state mutation, no side effects)."""

from __future__ import annotations

from typing import List, Optional, Sequence

from alerts.models import RuleResult


def _severity_for_level(abs_level: float) -> str:
    """Map threshold magnitude to a simple severity level."""
    if abs_level >= 20:
        return "high"
    if abs_level >= 10:
        return "medium"
    return "low"


def check_drawdown(symbol: str, drawdown_pct: Optional[float], levels: Sequence[float]) -> List[RuleResult]:
    """
    Return drawdown rule events for breached levels.

    `drawdown_pct` should be negative for drawdowns (e.g. -12.3).
    """
    if drawdown_pct is None:
        return []

    events: List[RuleResult] = []
    for level in sorted(levels):
        if drawdown_pct <= level:
            events.append(
                RuleResult(
                    key=f"{symbol}_drawdown",
                    alert_type="market_drawdown",
                    message=f"{symbol} drawdown reached {drawdown_pct:.2f}% (threshold {level:.0f}%).",
                    severity=_severity_for_level(abs(level)),
                    level=float(level),
                )
            )
    return events


def check_portfolio_drop(drop_pct: Optional[float], levels: Sequence[float]) -> List[RuleResult]:
    """
    Return portfolio drop rule events for breached levels.

    `drop_pct` should be negative for drops (e.g. -6.5).
    """
    if drop_pct is None:
        return []

    events: List[RuleResult] = []
    for level in sorted(levels):
        if drop_pct <= level:
            events.append(
                RuleResult(
                    key="portfolio_drop",
                    alert_type="portfolio_drop",
                    message=f"Portfolio dropped {drop_pct:.2f}% from peak (threshold {level:.0f}%).",
                    severity=_severity_for_level(abs(level)),
                    level=float(level),
                )
            )
    return events


def check_vix_spike(vix_value: Optional[float], threshold: float) -> List[RuleResult]:
    """Return a VIX spike event when value exceeds configured threshold."""
    if vix_value is None:
        return []
    if vix_value <= threshold:
        return []

    return [
        RuleResult(
            key="vix_spike",
            alert_type="vix_spike",
            message=f"VIX spiked to {vix_value:.2f} (threshold {threshold:.2f}).",
            severity="high" if vix_value >= threshold + 5 else "medium",
            level=float(threshold),
        )
    ]
