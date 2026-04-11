"""VWCE/CNDX split suggestion (read-only; uses Buying Ladder total only)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

from buying_ladder.logic import BuyingLadderResult
from buying_ladder.models import BuyingLadderSettings

logger = logging.getLogger("buying_ladder.allocation")


@dataclass(frozen=True)
class VwceCndxSplitResult:
    """Optional layer: how to split the ladder total between VWCE and CNDX."""

    show_ui_block: bool
    vwce_drawdown_pct: Optional[float]
    cndx_drawdown_pct: Optional[float]
    relative_gap_pct: Optional[float]
    vwce_weight: float
    cndx_weight: float
    vwce_amount: float
    cndx_amount: float
    allocation_label: str
    regime_label: str
    explanation_lines: Tuple[str, ...]


def _read_drawdown(market_df: Optional[pd.DataFrame], symbol: str) -> Optional[float]:
    if market_df is None or market_df.empty or "Symbol" not in market_df.columns:
        return None
    col = "Drawdown from ATH %"
    if col not in market_df.columns:
        return None
    rows = market_df.loc[market_df["Symbol"].astype(str).str.upper() == symbol.upper()]
    if rows.empty:
        return None
    raw = rows.iloc[0].get(col)
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _max_cndx_weight_for_vwce_regime(vwce_dd: float) -> float:
    if vwce_dd > -10.0:
        return 0.30
    if vwce_dd > -20.0:
        return 0.35
    return 0.45


def _weights_from_rules(vwce_dd: float, cndx_dd: float) -> Tuple[float, float, str]:
    """Return (vwce_w, cndx_w, case_key). Caller ensures cndx_dd <= -10."""
    gap = abs(cndx_dd) - abs(vwce_dd)

    if gap < 5.0:
        return 0.80, 0.20, "gap_lt_5"

    if gap < 10.0:
        if vwce_dd > -10.0:
            return 0.75, 0.25, "gap_5_10_shallow"
        if vwce_dd > -20.0:
            return 0.72, 0.28, "gap_5_10_moderate"
        return 0.70, 0.30, "gap_5_10_deep"

    if gap < 20.0:
        if vwce_dd > -10.0:
            return 0.70, 0.30, "gap_10_20_shallow"
        if vwce_dd > -20.0:
            return 0.65, 0.35, "gap_10_20_moderate"
        return 0.60, 0.40, "gap_10_20_deep"

    if vwce_dd > -10.0:
        return 0.70, 0.30, "gap_ge_20_shallow"
    if vwce_dd > -20.0:
        return 0.65, 0.35, "gap_ge_20_moderate"
    return 0.55, 0.45, "gap_ge_20_deep"


def _regime_description(vwce_dd: float) -> str:
    if vwce_dd > -10.0:
        return "shallow_global"
    if vwce_dd > -20.0:
        return "moderate_global"
    return "deep_global"


def _build_rule_explanations(vwce_dd: float, gap: float) -> list[str]:
    lines: list[str] = []
    if gap < 5.0:
        lines.append("Both indices are down by a similar amount; the default **80/20** split remains.")
        return lines

    if vwce_dd > -10.0:
        lines.append(
            "VWCE is only slightly below ATH, so the total stays conservative and any CNDX tilt is **capped**."
        )
    elif vwce_dd > -20.0:
        lines.append("Global drawdown (**moderate**) allows more room to tilt toward CNDX if it lags.")
    else:
        lines.append("Global drawdown is **deep**; a **stronger temporary tilt** toward CNDX is allowed when justified.")

    if gap >= 20.0:
        lines.append("CNDX has fallen **much more** than VWCE by drawdown magnitude.")
    elif gap >= 10.0:
        lines.append("CNDX shows **clear extra weakness** vs VWCE.")
    else:
        lines.append("CNDX shows **moderate extra weakness** vs VWCE.")

    return lines


def compute_vwce_cndx_split(
    settings: BuyingLadderSettings,
    ladder_result: BuyingLadderResult,
    market_df: Optional[pd.DataFrame],
) -> Optional[VwceCndxSplitResult]:
    """
    Suggest how to split ladder_result.recommended_monthly between VWCE and CNDX.

    Returns None when the UI should not show the allocation block at all.
    """
    if not settings.suggest_vwce_cndx_split:
        return None

    if not ladder_result.feature_enabled:
        return None

    total = float(ladder_result.recommended_monthly)
    if not math.isfinite(total) or total <= 0:
        return None

    vwce_dd = _read_drawdown(market_df, "VWCE")
    cndx_dd = _read_drawdown(market_df, "CNDX")

    gap: Optional[float] = None
    regime = "unknown"
    case_key = "fallback"
    lines: list[str] = []

    if vwce_dd is None or cndx_dd is None:
        vw_w, cndx_w = 0.80, 0.20
        if vwce_dd is None and cndx_dd is None:
            lines.append("VWCE and CNDX drawdown data are missing; using default **80% VWCE / 20% CNDX**.")
        elif vwce_dd is None:
            lines.append("VWCE drawdown is missing; using default **80% VWCE / 20% CNDX**.")
        else:
            lines.append("CNDX drawdown is missing; using default **80% VWCE / 20% CNDX**.")
        logger.info(
            "allocation: fallback_missing_dd vwce=%r cndx=%r total=%.2f -> 80/20",
            vwce_dd,
            cndx_dd,
            total,
        )
    elif cndx_dd > -10.0:
        vw_w, cndx_w = 0.80, 0.20
        gap = abs(cndx_dd) - abs(vwce_dd)
        regime = _regime_description(vwce_dd)
        case_key = "cndx_shallower_than_10pct"
        lines.append(
            f"CNDX is not down at least **10%** from ATH ({cndx_dd:.2f}%); no tactical tilt — **80/20**."
        )
        logger.info(
            "allocation: safety_cndx_shallow vwce=%.2f cndx=%.2f gap=%.2f regime=%s -> 80/20",
            vwce_dd,
            cndx_dd,
            gap,
            regime,
        )
    else:
        gap = abs(cndx_dd) - abs(vwce_dd)
        regime = _regime_description(vwce_dd)
        vw_w, cndx_w, case_key = _weights_from_rules(vwce_dd, cndx_dd)
        max_c = _max_cndx_weight_for_vwce_regime(vwce_dd)
        capped = False
        if cndx_w > max_c:
            cndx_w = max_c
            vw_w = 1.0 - cndx_w
            capped = True
        lines.extend(_build_rule_explanations(vwce_dd, gap))
        if capped:
            lines.append(
                f"Applied **regime cap**: at most **{max_c * 100:.0f}%** to CNDX for this VWCE drawdown band."
            )
        logger.info(
            "allocation: vwce=%.2f cndx=%.2f gap=%.2f regime=%s case=%s weights=%.2f/%.2f vwce_amt=%.2f cndx_amt=%.2f capped=%s",
            vwce_dd,
            cndx_dd,
            gap,
            regime,
            case_key,
            vw_w,
            cndx_w,
            total * vw_w,
            total * cndx_w,
            capped,
        )

    vw_amt = round(total * vw_w, 2)
    cndx_amt = round(total * cndx_w, 2)
    drift = round(total - vw_amt - cndx_amt, 2)
    if abs(drift) >= 0.01:
        vw_amt = round(vw_amt + drift, 2)

    pct = lambda x: int(round(x * 100))
    label = f"{pct(vw_w)}% VWCE / {pct(cndx_w)}% CNDX"

    return VwceCndxSplitResult(
        show_ui_block=True,
        vwce_drawdown_pct=vwce_dd,
        cndx_drawdown_pct=cndx_dd,
        relative_gap_pct=gap,
        vwce_weight=vw_w,
        cndx_weight=cndx_w,
        vwce_amount=vw_amt,
        cndx_amount=cndx_amt,
        allocation_label=label,
        regime_label=regime,
        explanation_lines=tuple(lines),
    )
