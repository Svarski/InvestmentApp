"""Data models and defaults for the optional buying ladder feature."""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ContributionPhase:
    """Monthly base contribution for a calendar period of the plan (by year number from start)."""

    label: str
    year_start: int
    year_end: int
    monthly_amount: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["ContributionPhase"]:
        if not isinstance(data, dict):
            return None
        try:
            monthly = float(data.get("monthly_amount", 0.0))
            if not math.isfinite(monthly) or monthly < 0:
                monthly = 0.0
            return cls(
                label=_clean_text_label(data.get("label", "")),
                year_start=int(data.get("year_start", 1)),
                year_end=int(data.get("year_end", 99)),
                monthly_amount=monthly,
            )
        except (TypeError, ValueError) as exc:
            logger.debug("Skipping invalid contribution phase row: %s error=%s", data, exc)
            return None


@dataclass
class LadderStep:
    """
    When drawdown is at or below `drawdown_threshold_pct`, this step is a candidate.
    The active step is the matching threshold that is most negative (deepest drawdown band).
    Thresholds are negative or zero (e.g. -20 means -20% from ATH or worse).
    """

    drawdown_threshold_pct: float
    multiplier: float = 1.0
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["LadderStep"]:
        if not isinstance(data, dict):
            return None
        try:
            thresh = float(data.get("drawdown_threshold_pct", 0.0))
            mult = float(data.get("multiplier", 1.0))
            if not math.isfinite(thresh) or not math.isfinite(mult):
                return None
            if mult < 0:
                mult = 0.0
            return cls(
                drawdown_threshold_pct=thresh,
                multiplier=mult,
                label=_clean_text_label(data.get("label", "")),
            )
        except (TypeError, ValueError) as exc:
            logger.debug("Skipping invalid ladder step row: %s error=%s", data, exc)
            return None


@dataclass
class BuyingLadderSettings:
    """User-controlled buying ladder configuration (persisted)."""

    enabled: bool = False
    benchmark_symbol: str = "VWCE"
    phase_selection_mode: str = "elapsed"
    plan_start_date: Optional[str] = None
    manual_phase_index: int = 0
    phases: Tuple[ContributionPhase, ...] = field(default_factory=tuple)
    ladder_steps: Tuple[LadderStep, ...] = field(default_factory=tuple)
    show_calculation_details: bool = True
    crash_extra_equity_pct: Optional[float] = None
    include_buying_ladder_in_weekly_summary: bool = False
    suggest_vwce_cndx_split: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "benchmark_symbol": self.benchmark_symbol,
            "phase_selection_mode": self.phase_selection_mode,
            "plan_start_date": self.plan_start_date,
            "manual_phase_index": self.manual_phase_index,
            "phases": [p.to_dict() for p in self.phases],
            "ladder_steps": [s.to_dict() for s in self.ladder_steps],
            "show_calculation_details": self.show_calculation_details,
            "crash_extra_equity_pct": self.crash_extra_equity_pct,
            "include_buying_ladder_in_weekly_summary": self.include_buying_ladder_in_weekly_summary,
            "suggest_vwce_cndx_split": self.suggest_vwce_cndx_split,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BuyingLadderSettings":
        try:
            if not data or not isinstance(data, dict):
                return default_buying_ladder_settings()

            phases_raw = data.get("phases")
            steps_raw = data.get("ladder_steps")
            phases_list: List[ContributionPhase] = []
            if isinstance(phases_raw, list):
                for item in phases_raw:
                    if not isinstance(item, dict):
                        continue
                    phase = ContributionPhase.from_dict(item)
                    if phase is not None:
                        phases_list.append(phase)
            phases = tuple(phases_list) if phases_list else default_phases()

            steps_list: List[LadderStep] = []
            if isinstance(steps_raw, list):
                for item in steps_raw:
                    if not isinstance(item, dict):
                        continue
                    step = LadderStep.from_dict(item)
                    if step is not None:
                        steps_list.append(_migrate_legacy_ladder_step(step))
            steps = tuple(steps_list) if steps_list else default_ladder_steps()

            mode = str(data.get("phase_selection_mode", "elapsed")).strip().lower()
            if mode not in {"elapsed", "manual"}:
                mode = "elapsed"

            crash_pct = data.get("crash_extra_equity_pct")
            crash_pct_f: Optional[float]
            if crash_pct is None or crash_pct == "":
                crash_pct_f = None
            else:
                try:
                    crash_pct_f = float(crash_pct)
                except (TypeError, ValueError):
                    crash_pct_f = None

            manual_idx = int(data.get("manual_phase_index", 0))

            return cls(
                enabled=bool(data.get("enabled", False)),
                benchmark_symbol=str(data.get("benchmark_symbol", "VWCE")).strip().upper() or "VWCE",
                phase_selection_mode=mode,
                plan_start_date=_normalize_date_str(data.get("plan_start_date")),
                manual_phase_index=manual_idx,
                phases=phases,
                ladder_steps=steps,
                show_calculation_details=bool(data.get("show_calculation_details", True)),
                crash_extra_equity_pct=crash_pct_f,
                include_buying_ladder_in_weekly_summary=bool(
                    data.get("include_buying_ladder_in_weekly_summary", False)
                ),
                suggest_vwce_cndx_split=bool(data.get("suggest_vwce_cndx_split", False)),
            )
        except Exception as exc:
            logger.warning("BuyingLadderSettings.from_dict failed; using full defaults. error=%s", exc)
            return default_buying_ladder_settings()


def _clean_text_label(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def _normalize_date_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _migrate_legacy_ladder_step(step: LadderStep) -> LadderStep:
    """Fill empty labels for JSON saved before `label` existed."""
    if step.label:
        return step
    for threshold, name in (
        (0.0, "Normal"),
        (-10.0, "Pullback"),
        (-20.0, "Correction"),
        (-30.0, "Deep Correction"),
        (-40.0, "Crash"),
        (-50.0, "Extreme Crash"),
    ):
        if abs(step.drawdown_threshold_pct - threshold) < 1e-6:
            return LadderStep(
                drawdown_threshold_pct=step.drawdown_threshold_pct,
                multiplier=step.multiplier,
                label=name,
            )
    return step


def default_phases() -> Tuple[ContributionPhase, ...]:
    return (
        ContributionPhase(label="Years 1–5", year_start=1, year_end=5, monthly_amount=350.0),
        ContributionPhase(label="Years 6–10", year_start=6, year_end=10, monthly_amount=550.0),
        ContributionPhase(label="Years 11–20", year_start=11, year_end=20, monthly_amount=700.0),
    )


def default_ladder_steps() -> Tuple[LadderStep, ...]:
    return (
        LadderStep(label="Normal", drawdown_threshold_pct=0.0, multiplier=1.0),
        LadderStep(label="Pullback", drawdown_threshold_pct=-10.0, multiplier=1.25),
        LadderStep(label="Correction", drawdown_threshold_pct=-20.0, multiplier=1.6),
        LadderStep(label="Deep Correction", drawdown_threshold_pct=-30.0, multiplier=2.0),
        LadderStep(label="Crash", drawdown_threshold_pct=-40.0, multiplier=2.75),
        LadderStep(label="Extreme Crash", drawdown_threshold_pct=-50.0, multiplier=3.25),
    )


def default_buying_ladder_settings() -> BuyingLadderSettings:
    return BuyingLadderSettings(
        enabled=False,
        benchmark_symbol="VWCE",
        phase_selection_mode="elapsed",
        plan_start_date=None,
        manual_phase_index=0,
        phases=default_phases(),
        ladder_steps=default_ladder_steps(),
        show_calculation_details=True,
        crash_extra_equity_pct=None,
        include_buying_ladder_in_weekly_summary=False,
        suggest_vwce_cndx_split=False,
    )


def merge_with_defaults(settings: BuyingLadderSettings) -> BuyingLadderSettings:
    """Ensure non-empty phases and ladder steps; clamp manual phase index."""
    phases = settings.phases if settings.phases else default_phases()
    steps = settings.ladder_steps if settings.ladder_steps else default_ladder_steps()
    clamped_idx = max(0, min(int(settings.manual_phase_index), max(0, len(phases) - 1)))
    return BuyingLadderSettings(
        enabled=settings.enabled,
        benchmark_symbol=settings.benchmark_symbol,
        phase_selection_mode=settings.phase_selection_mode,
        plan_start_date=settings.plan_start_date,
        manual_phase_index=clamped_idx,
        phases=phases,
        ladder_steps=steps,
        show_calculation_details=settings.show_calculation_details,
        crash_extra_equity_pct=settings.crash_extra_equity_pct,
        include_buying_ladder_in_weekly_summary=settings.include_buying_ladder_in_weekly_summary,
        suggest_vwce_cndx_split=settings.suggest_vwce_cndx_split,
    )


def phases_from_rows(rows: List[Dict[str, Any]]) -> Tuple[ContributionPhase, ...]:
    out: List[ContributionPhase] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        phase = ContributionPhase.from_dict(row)
        if phase is not None:
            out.append(phase)
    return tuple(out) if out else default_phases()


def ladder_steps_from_rows(rows: List[Dict[str, Any]]) -> Tuple[LadderStep, ...]:
    out: List[LadderStep] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        step = LadderStep.from_dict(row)
        if step is not None:
            out.append(step)
    return tuple(out) if out else default_ladder_steps()
