"""Buying ladder recommendation logic (read-only decision support)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional, Sequence, Tuple

import pandas as pd

from buying_ladder.models import BuyingLadderSettings, ContributionPhase, LadderStep, merge_with_defaults

logger = logging.getLogger("buying_ladder.logic")


@dataclass(frozen=True)
class BuyingLadderResult:
    """Outcome of a buying ladder calculation for display and logging."""

    feature_enabled: bool
    benchmark_symbol: str
    drawdown_pct: Optional[float]
    ladder_step_label: str
    phase_label: str
    phase_index: int
    base_monthly: float
    ladder_threshold_pct: float
    multiplier: float
    recommended_monthly: float
    extra_vs_base: float
    explanation_lines: Tuple[str, ...]
    drawdown_missing: bool
    benchmark_missing: bool
    phase_fallback: bool


def display_step_label(step: LadderStep) -> str:
    """Human-readable ladder step name for UI and weekly text."""
    if step.label and str(step.label).strip():
        return str(step.label).strip()
    t = step.drawdown_threshold_pct
    return f"<= {t:.0f}% band"


def _parse_plan_start(iso_date: Optional[str]) -> Optional[date]:
    if not iso_date:
        return None
    text = str(iso_date).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        logger.debug("Invalid plan_start_date=%r", iso_date)
        return None


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _plan_year_number(plan_start: date, today: date) -> int:
    """1-based plan year: year 1 is the first full interval [0, 1) years from start."""
    if today < plan_start:
        return 1
    delta_days = (today - plan_start).days
    elapsed_years = delta_days / 365.25
    return int(elapsed_years) + 1


def _select_phase_elapsed(
    phases: Sequence[ContributionPhase], plan_start: Optional[date], today: date
) -> Tuple[ContributionPhase, int, bool]:
    """Return active phase, index, and whether a fallback was used. First matching range wins if overlaps exist."""
    if not phases:
        return ContributionPhase(label="Default", year_start=1, year_end=999, monthly_amount=0.0), 0, True
    if plan_start is None:
        logger.debug("Buying ladder: no plan_start_date; using first phase as fallback.")
        return phases[0], 0, True
    year_n = _plan_year_number(plan_start, today)
    for idx, phase in enumerate(phases):
        if phase.year_start <= year_n <= phase.year_end:
            return phase, idx, False
    if year_n < phases[0].year_start:
        return phases[0], 0, False
    last = phases[-1]
    return last, len(phases) - 1, False


def _select_phase_manual(phases: Sequence[ContributionPhase], index: int) -> Tuple[ContributionPhase, int]:
    if not phases:
        return ContributionPhase(label="Default", year_start=1, year_end=999, monthly_amount=0.0), 0
    idx = max(0, min(int(index), len(phases) - 1))
    return phases[idx], idx


def _benchmark_row_exists(market_df: Optional[pd.DataFrame], symbol: str) -> bool:
    if market_df is None or market_df.empty or "Symbol" not in market_df.columns:
        return False
    rows = market_df.loc[market_df["Symbol"].astype(str).str.upper() == symbol.upper()]
    return not rows.empty


def _get_drawdown_for_symbol(market_df: Optional[pd.DataFrame], symbol: str) -> Optional[float]:
    if market_df is None or market_df.empty or "Symbol" not in market_df.columns:
        return None
    col = "Drawdown from ATH %"
    if col not in market_df.columns:
        logger.debug("Buying ladder: column %r missing on market dataframe.", col)
        return None
    rows = market_df.loc[market_df["Symbol"].astype(str).str.upper() == symbol.upper()]
    if rows.empty:
        return None
    raw = rows.iloc[0].get(col)
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _baseline_step(steps: Sequence[LadderStep]) -> LadderStep:
    if not steps:
        return LadderStep(label="Normal", drawdown_threshold_pct=0.0, multiplier=1.0)
    sorted_steps = sorted(steps, key=lambda s: s.drawdown_threshold_pct)
    for step in sorted_steps:
        if step.drawdown_threshold_pct == 0.0:
            return step
    return sorted_steps[0]


def _select_ladder_step(drawdown_pct: Optional[float], steps: Sequence[LadderStep]) -> Tuple[LadderStep, bool]:
    """
    Pick the step with the most negative threshold such that drawdown_pct <= threshold.
    Steps are sorted by threshold so unsorted persisted data still behaves deterministically.
    """
    if not steps:
        return LadderStep(label="Normal", drawdown_threshold_pct=0.0, multiplier=1.0), True
    sorted_steps = sorted(steps, key=lambda s: s.drawdown_threshold_pct)
    if drawdown_pct is None:
        return _baseline_step(sorted_steps), True
    candidates = [s for s in sorted_steps if drawdown_pct <= s.drawdown_threshold_pct]
    if not candidates:
        return _baseline_step(sorted_steps), False
    chosen = min(candidates, key=lambda s: s.drawdown_threshold_pct)
    return chosen, False


def compute_buying_ladder(
    settings: BuyingLadderSettings,
    market_df: Optional[pd.DataFrame],
    *,
    today: Optional[date] = None,
) -> BuyingLadderResult:
    """
    Compute recommended monthly contribution from settings and market drawdown.

    Does not mutate portfolio, alerts, or market data. Safe with partial inputs.
    """
    try:
        raw_manual_idx = int(settings.manual_phase_index)
    except (TypeError, ValueError):
        raw_manual_idx = 0
    merged = merge_with_defaults(settings)
    if not merged.enabled:
        return BuyingLadderResult(
            feature_enabled=False,
            benchmark_symbol=merged.benchmark_symbol,
            drawdown_pct=None,
            ladder_step_label="",
            phase_label="",
            phase_index=0,
            base_monthly=0.0,
            ladder_threshold_pct=0.0,
            multiplier=1.0,
            recommended_monthly=0.0,
            extra_vs_base=0.0,
            explanation_lines=("Buying ladder is disabled in settings.",),
            drawdown_missing=False,
            benchmark_missing=False,
            phase_fallback=False,
        )

    today_d = today or _today_utc()
    symbol = merged.benchmark_symbol
    benchmark_missing = not _benchmark_row_exists(market_df, symbol)
    drawdown = _get_drawdown_for_symbol(market_df, symbol)
    drawdown_missing = drawdown is None

    manual_clamped = False
    if merged.phase_selection_mode == "manual":
        phase, phase_index = _select_phase_manual(merged.phases, merged.manual_phase_index)
        n_ph = len(merged.phases)
        if n_ph > 0:
            bounded = max(0, min(raw_manual_idx, n_ph - 1))
            manual_clamped = raw_manual_idx != bounded
        phase_fallback = manual_clamped
    else:
        plan_start = _parse_plan_start(merged.plan_start_date)
        phase, phase_index, phase_fallback = _select_phase_elapsed(merged.phases, plan_start, today_d)

    try:
        base = float(phase.monthly_amount)
    except (TypeError, ValueError):
        base = 0.0
    if not math.isfinite(base) or base < 0:
        base = 0.0
    lines: List[str] = []

    if drawdown_missing:
        step = _baseline_step(merged.ladder_steps)
        mult = 1.0
        recommended = round(base * mult, 2)
        if benchmark_missing:
            lines.append(
                f"No market row for benchmark {symbol}; cannot read drawdown. Using base {base:,.2f} at 1.0x."
            )
        elif market_df is not None and not market_df.empty and "Drawdown from ATH %" not in market_df.columns:
            lines.append("Drawdown column missing from market data; using base at 1.0x.")
        else:
            lines.append(
                f"Drawdown for {symbol} is unavailable (NaN or empty); using base {base:,.2f} at 1.0x."
            )
    else:
        step, _ = _select_ladder_step(drawdown, merged.ladder_steps)
        mult = float(step.multiplier)
        recommended = round(base * mult, 2)
        lines.append(f"{symbol} drawdown: **{drawdown:.2f}%** from ATH.")
        lines.append(
            f"Active step **{display_step_label(step)}** (<= {step.drawdown_threshold_pct:.0f}% band, **{mult:.2f}x**)."
        )

    extra = round(recommended - base, 2)
    step_label = display_step_label(step)

    if phase_fallback and merged.phase_selection_mode == "elapsed" and not merged.plan_start_date:
        lines.append("Automatic phases need a plan start date; using the first phase for the base amount.")
    elif phase_fallback and merged.phase_selection_mode == "manual":
        lines.append("Manual phase index was out of range; it was clamped to a valid phase.")

    lines.append(f"Phase **{phase.label or f'Phase {phase_index + 1}'}**: base **{base:,.2f}**/mo → **{recommended:,.2f}** recommended (+**{extra:,.2f}** vs base).")

    if merged.crash_extra_equity_pct is not None:
        pct = merged.crash_extra_equity_pct
        lines.append(f"Optional tilt: ~{pct:.0f}% of extra capital toward equities (your note).")

    logger.debug(
        "Buying ladder detail: benchmark=%s drawdown=%s benchmark_missing=%s phase=%s step=%s base=%s mult=%s rec=%s extra=%s",
        symbol,
        drawdown,
        benchmark_missing,
        phase.label,
        step_label,
        base,
        mult,
        recommended,
        extra,
    )
    logger.info(
        "Buying ladder: benchmark=%s drawdown=%s step=%s phase=%s base=%.2f mult=%.2f recommended=%.2f extra_vs_base=%.2f",
        symbol,
        f"{drawdown:.2f}" if drawdown is not None else "None",
        step_label,
        phase.label or phase_index,
        base,
        mult,
        recommended,
        extra,
    )

    return BuyingLadderResult(
        feature_enabled=True,
        benchmark_symbol=symbol,
        drawdown_pct=drawdown,
        ladder_step_label=step_label,
        phase_label=phase.label or f"Phase {phase_index + 1}",
        phase_index=phase_index,
        base_monthly=base,
        ladder_threshold_pct=step.drawdown_threshold_pct,
        multiplier=mult,
        recommended_monthly=recommended,
        extra_vs_base=extra,
        explanation_lines=tuple(lines),
        drawdown_missing=drawdown_missing,
        benchmark_missing=benchmark_missing,
        phase_fallback=phase_fallback,
    )
