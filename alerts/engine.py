"""Core alert engine: detection + decision/dedup/reset."""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

import pandas as pd

from alerts.config import AlertSettings
from alerts.models import Alert, RuleResult
from alerts.rules import check_drawdown, check_portfolio_drop, check_vix_spike
from alerts.settings_loader import get_alert_settings
from alerts.state import AlertState

logger = logging.getLogger(__name__)


class AlertEngine:
    """Evaluate alert rules against market/portfolio data with stateful dedupe."""

    def __init__(self, settings: Optional[AlertSettings] = None, state: Optional[AlertState] = None) -> None:
        self.settings = settings or get_alert_settings()
        self.state = state or AlertState()

    def evaluate(self, market_df: pd.DataFrame, portfolio_value: Optional[float]) -> List[Alert]:
        """
        Evaluate all alert rules and return newly triggered alerts.

        Parameters:
            market_df: expected to contain `Symbol`, `Drawdown from ATH %`, and optionally VIX `Price`.
            portfolio_value: current total portfolio value.
        """
        results: List[RuleResult] = []
        results.extend(self._evaluate_market_drawdowns(market_df))
        results.extend(self._evaluate_portfolio_drop(portfolio_value))
        results.extend(self._evaluate_vix(market_df))

        alerts = self._apply_decisions(results, market_df=market_df, portfolio_value=portfolio_value)
        return alerts

    def _evaluate_market_drawdowns(self, market_df: pd.DataFrame) -> List[RuleResult]:
        if market_df is None or market_df.empty:
            return []

        events: List[RuleResult] = []
        allowed_symbols = {symbol.upper() for symbol in self.settings.drawdown_alert_symbols}
        for _, row in market_df.iterrows():
            symbol = row.get("Symbol")
            drawdown = _safe_float(row.get("Drawdown from ATH %"))
            if not symbol:
                continue
            symbol_str = str(symbol).upper()

            logger.debug("Drawdown check symbol=%s drawdown=%s", symbol_str, drawdown)

            if symbol_str not in allowed_symbols:
                continue

            events.extend(check_drawdown(symbol_str, drawdown, self.settings.drawdown_levels))

        return events

    def _evaluate_portfolio_drop(self, portfolio_value: Optional[float]) -> List[RuleResult]:
        value = _safe_float(portfolio_value)
        if value is None or value <= 0:
            return []

        peak = self.state.get_metric("portfolio_peak_value")
        if peak is None or value > peak:
            self.state.set_metric("portfolio_peak_value", value)
            peak = value

        if peak <= 0:
            return []

        drop_pct = ((value - peak) / peak) * 100.0
        return check_portfolio_drop(drop_pct, self.settings.portfolio_drop_levels)

    def _evaluate_vix(self, market_df: pd.DataFrame) -> List[RuleResult]:
        if market_df is None or market_df.empty:
            return []

        vix_rows = market_df.loc[market_df["Symbol"] == "VIX"] if "Symbol" in market_df.columns else pd.DataFrame()
        if vix_rows.empty:
            return []

        vix_value = _safe_float(vix_rows.iloc[0].get("Price"))
        return check_vix_spike(vix_value, self.settings.vix_spike_threshold)

    def _apply_decisions(
        self,
        rule_events: Iterable[RuleResult],
        market_df: pd.DataFrame,
        portfolio_value: Optional[float],
    ) -> List[Alert]:
        """
        Decide what to alert now: dedupe + reset + trigger.

        Detection is driven by `rule_events`; reset checks use current values.
        """
        alerts: List[Alert] = []
        event_list = list(rule_events)

        # Trigger new events if level was not already triggered
        for event in event_list:
            if event.level is None:
                continue

            if not self.state.is_level_triggered(event.key, event.level):
                self.state.mark_triggered(event.key, event.level)
                alert_id = f"{event.key}:{event.level:.2f}"
                logger.info("Alert triggered id=%s type=%s", alert_id, event.alert_type)
                alerts.append(
                    Alert(
                        id=alert_id,
                        type=event.alert_type,
                        message=event.message,
                        severity=event.severity,
                    )
                )
            else:
                logger.info("Alert deduplicated key=%s level=%s", event.key, event.level)

        # Reset previously triggered levels when conditions recover
        self._apply_resets(market_df=market_df, portfolio_value=portfolio_value)
        return alerts

    def _apply_resets(self, market_df: pd.DataFrame, portfolio_value: Optional[float]) -> None:
        """Reset triggered levels after recovery to allow future alerts."""
        self._reset_drawdowns(market_df)
        self._reset_portfolio_drop(portfolio_value)
        self._reset_vix(market_df)

    def _reset_drawdowns(self, market_df: pd.DataFrame) -> None:
        if market_df is None or market_df.empty:
            return
        if "Symbol" not in market_df.columns or "Drawdown from ATH %" not in market_df.columns:
            return

        allowed_symbols = {symbol.upper() for symbol in self.settings.drawdown_alert_symbols}
        for _, row in market_df.iterrows():
            symbol = row.get("Symbol")
            if not symbol:
                continue
            symbol_str = str(symbol).upper()
            if symbol_str not in allowed_symbols:
                continue
            current_drawdown = _safe_float(row.get("Drawdown from ATH %"))
            if current_drawdown is None:
                continue

            key = f"{symbol_str}_drawdown"
            configured_levels = sorted(self.settings.drawdown_levels, reverse=True)  # e.g. [-10, -20, -30]
            for level in list(self.state.get_triggered_levels(key)):
                # Use stronger recovery for the mildest level so it does not stay stuck forever.
                # Example with levels [-10, -20, -30] and buffer=5:
                # -20 resets above -15; -10 resets above -15 (via next deeper level).
                if level in configured_levels:
                    level_index = configured_levels.index(level)
                    if level_index == 0 and len(configured_levels) > 1:
                        next_deeper_level = configured_levels[1]
                        reset_threshold = next_deeper_level + self.settings.drawdown_reset_buffer
                    else:
                        reset_threshold = level + self.settings.drawdown_reset_buffer
                else:
                    reset_threshold = level + self.settings.drawdown_reset_buffer

                if current_drawdown > reset_threshold:
                    logger.info("Reset drawdown alert key=%s level=%s", key, level)
                    self.state.reset_level(key, level)

    def _reset_portfolio_drop(self, portfolio_value: Optional[float]) -> None:
        value = _safe_float(portfolio_value)
        peak = self.state.get_metric("portfolio_peak_value")
        if value is None or peak is None or peak <= 0:
            return

        drop_pct = ((value - peak) / peak) * 100.0
        key = "portfolio_drop"
        for level in list(self.state.get_triggered_levels(key)):
            reset_threshold = level + self.settings.portfolio_reset_buffer
            if drop_pct > reset_threshold:
                logger.info("Reset portfolio drop alert level=%s", level)
                self.state.reset_level(key, level)

    def _reset_vix(self, market_df: pd.DataFrame) -> None:
        if market_df is None or market_df.empty or "Symbol" not in market_df.columns:
            return

        vix_rows = market_df.loc[market_df["Symbol"] == "VIX"]
        if vix_rows.empty:
            return

        vix_value = _safe_float(vix_rows.iloc[0].get("Price"))
        if vix_value is None:
            return

        key = "vix_spike"
        for level in list(self.state.get_triggered_levels(key)):
            reset_threshold = level - self.settings.vix_reset_buffer
            if vix_value < reset_threshold:
                logger.info("Reset VIX spike alert level=%s", level)
                self.state.reset_level(key, level)


def _safe_float(value: object) -> Optional[float]:
    """Safely cast values to float for alert evaluation."""
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
