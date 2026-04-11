"""Optional weekly digest section for buying ladder (worker/UI-agnostic)."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import pandas as pd

from buying_ladder.logic import compute_buying_ladder
from buying_ladder.models import merge_with_defaults
from buying_ladder.storage import load_buying_ladder_settings

logger = logging.getLogger("buying_ladder.weekly")


def build_buying_ladder_weekly_appendix(market_df: Optional[pd.DataFrame]) -> Tuple[Optional[str], str]:
    """
    Build plain-text lines for section "4) Buying ladder", or None if the weekly flag is off.

    Returns (appendix_text, omit_reason) for logging. Does not raise.
    """
    try:
        settings = merge_with_defaults(load_buying_ladder_settings())
    except Exception as exc:
        logger.warning("Buying ladder weekly appendix skipped (settings load error). error=%s", exc)
        return None, "settings_load_error"

    if not settings.include_buying_ladder_in_weekly_summary:
        logger.info("Buying ladder weekly appendix omitted: include_buying_ladder_in_weekly_summary=false")
        return None, "weekly_inclusion_disabled"

    if not settings.enabled:
        text = (
            "4) Buying ladder\n"
            "- Weekly section is enabled, but the buying ladder is off in settings (no recommendation).\n"
        )
        logger.info("Buying ladder weekly appendix: feature_disabled_minimal_note")
        return text, "feature_disabled"

    try:
        result = compute_buying_ladder(settings, market_df)
    except Exception as exc:
        logger.warning("Buying ladder weekly appendix skipped (compute error). error=%s", exc)
        return (
            "4) Buying ladder\n"
            "- Could not compute a recommendation this week (see worker logs).\n",
            "compute_error",
        )

    if not result.feature_enabled:
        return (
            "4) Buying ladder\n"
            "- Buying ladder is disabled (no recommendation).\n",
            "feature_disabled",
        )

    dd = "N/A" if result.drawdown_pct is None else f"{result.drawdown_pct:.2f}%"
    reason = (
        f"Drawdown for {result.benchmark_symbol} is {dd}; "
        f"active step '{result.ladder_step_label}' applies {result.multiplier:.2f}x to the phase base."
    )
    if result.drawdown_missing:
        reason = (
            f"Drawdown for {result.benchmark_symbol} was unavailable; base amount kept at 1.0x "
            f"({result.base_monthly:,.2f}/mo)."
        )

    lines = [
        "4) Buying ladder",
        f"- Benchmark: {result.benchmark_symbol}",
        f"- Drawdown (ATH): {dd}",
        f"- Active step: {result.ladder_step_label}",
        f"- Active phase: {result.phase_label}",
        f"- Base monthly: {result.base_monthly:,.2f}",
        f"- Recommended now: {result.recommended_monthly:,.2f}",
        f"- Extra vs base: {result.extra_vs_base:+,.2f}",
        f"- Note: {reason}",
    ]
    text = "\n".join(lines) + "\n"
    logger.info(
        "Buying ladder weekly appendix included: benchmark=%s step=%s recommended=%.2f",
        result.benchmark_symbol,
        result.ladder_step_label,
        result.recommended_monthly,
    )
    return text, "included"
