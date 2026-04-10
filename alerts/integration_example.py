"""Example wiring for alert engine and notifier.

This module is intentionally not imported by the app automatically.
Use it as a reference to integrate alerting into the existing Streamlit flow.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from alerts.config import AlertSettings
from alerts.engine import AlertEngine
from alerts.notifier import AlertNotifier
from alerts.state import AlertState


def run_alert_cycle(
    market_overview_df: pd.DataFrame,
    portfolio_total_value: Optional[float],
    settings: AlertSettings,
    state: AlertState,
) -> None:
    """
    Evaluate alerts and send notifications for one application cycle.

    Usage in app flow:
      1) build market overview dataframe
      2) compute current portfolio total value
      3) call this function
    """
    engine = AlertEngine(settings=settings, state=state)
    notifier = AlertNotifier(settings=settings)

    triggered_alerts = engine.evaluate(
        market_df=market_overview_df,
        portfolio_value=portfolio_total_value,
    )
    notifier.send_alerts(triggered_alerts)
