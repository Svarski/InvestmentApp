"""Streamlit sidebar settings and dashboard card for the buying ladder."""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

import pandas as pd
import streamlit as st

from buying_ladder.allocation import compute_vwce_cndx_split
from buying_ladder.logic import compute_buying_ladder
from buying_ladder.models import (
    BuyingLadderSettings,
    ladder_steps_from_rows,
    merge_with_defaults,
    phases_from_rows,
)
from buying_ladder.storage import load_buying_ladder_settings, save_buying_ladder_settings
from config import TRACKED_INSTRUMENTS


def _benchmark_symbol_options() -> List[str]:
    return list(TRACKED_INSTRUMENTS.keys())


def _phases_dataframe(settings: BuyingLadderSettings) -> pd.DataFrame:
    return pd.DataFrame([asdict(p) for p in settings.phases])


def _steps_dataframe(settings: BuyingLadderSettings) -> pd.DataFrame:
    return pd.DataFrame([asdict(s) for s in settings.ladder_steps])


def _ensure_seed_dataframes(settings: BuyingLadderSettings) -> None:
    """
    Store editable tables under keys that are NOT the data_editor widget keys.
    Streamlit forbids setting st.session_state[widget_key] before the widget runs.
    """
    if "bl_phases_seed" not in st.session_state:
        legacy = st.session_state.get("bl_phases_editor")
        if isinstance(legacy, pd.DataFrame):
            st.session_state.bl_phases_seed = legacy.copy()
            del st.session_state.bl_phases_editor
        else:
            st.session_state.bl_phases_seed = _phases_dataframe(settings)
    if "bl_steps_seed" not in st.session_state:
        legacy_s = st.session_state.get("bl_steps_editor")
        if isinstance(legacy_s, pd.DataFrame):
            st.session_state.bl_steps_seed = legacy_s.copy()
            del st.session_state.bl_steps_editor
        else:
            st.session_state.bl_steps_seed = _steps_dataframe(settings)


def _phases_df_for_sidebar() -> pd.DataFrame:
    return st.session_state.bl_phases_seed


def _steps_df_for_sidebar() -> pd.DataFrame:
    return st.session_state.bl_steps_seed


def _current_phases_row_count() -> int:
    edited = st.session_state.get("bl_phases_editor")
    if isinstance(edited, pd.DataFrame):
        return max(1, len(edited.index))
    return max(1, len(st.session_state.bl_phases_seed.index))


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _fmt_gap(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f} pp"


def render_buying_ladder_sidebar() -> None:
    """Dedicated sidebar block: configure and persist buying ladder settings."""
    st.sidebar.divider()
    st.sidebar.subheader("Buying ladder")
    st.sidebar.caption("Optional decision support. Does not place trades or change alerts.")

    current = merge_with_defaults(load_buying_ladder_settings())
    _ensure_seed_dataframes(current)

    enabled = st.sidebar.checkbox("Buying ladder enabled", value=current.enabled, key="bl_enabled")

    options = _benchmark_symbol_options()
    default_idx = options.index(current.benchmark_symbol) if current.benchmark_symbol in options else 0
    benchmark = st.sidebar.selectbox(
        "Benchmark (drawdown from ATH)",
        options=options,
        index=default_idx,
        help="Recommendation uses this symbol's “Drawdown from ATH %” from the market overview.",
        key="bl_benchmark",
    )

    st.sidebar.radio(
        "Contribution phase selection",
        options=("elapsed", "manual"),
        index=0 if current.phase_selection_mode == "elapsed" else 1,
        format_func=lambda m: "From plan start date (automatic)" if m == "elapsed" else "Manual phase",
        key="bl_phase_mode",
    )
    mode = str(st.session_state.bl_phase_mode)

    plan_start = st.sidebar.text_input(
        "Plan start date (YYYY-MM-DD)",
        value=current.plan_start_date or "",
        help="Automatic mode: plan year 1 starts here. Elapsed years pick which phase row applies.",
        key="bl_plan_start",
    )

    phase_count = _current_phases_row_count()
    max_idx = max(0, phase_count - 1)
    manual_idx = st.sidebar.number_input(
        "Manual phase index (0-based)",
        min_value=0,
        max_value=max_idx,
        value=min(current.manual_phase_index, max_idx),
        help="Manual mode: which phase row defines the base monthly amount.",
        key="bl_manual_phase_idx",
    )

    show_details = st.sidebar.checkbox(
        "Show “Why this number?” on dashboard",
        value=current.show_calculation_details,
        key="bl_show_details",
    )

    include_weekly = st.sidebar.checkbox(
        "Include buying ladder in weekly email digest",
        value=current.include_buying_ladder_in_weekly_summary,
        help="When the worker sends the weekly summary, appends a short buying ladder section if enabled.",
        key="bl_include_weekly",
    )

    suggest_split = st.sidebar.checkbox(
        "Suggest VWCE / CNDX split in insight",
        value=current.suggest_vwce_cndx_split,
        help="Adds a suggested allocation of the recommended monthly amount (same card; uses VWCE & CNDX drawdowns).",
        key="bl_suggest_split",
    )

    crash_pct_raw = st.sidebar.text_input(
        "Optional: extra to equities (%)",
        value="" if current.crash_extra_equity_pct is None else str(current.crash_extra_equity_pct),
        help="Informational only: how you prefer to tilt extra contributions in deep drawdowns.",
        key="bl_crash_pct",
    )

    st.sidebar.markdown("**Contribution phases**")
    st.sidebar.caption(
        "Normal monthly amount for each plan period. Plan years count from your start date (automatic) "
        "or from the row you pick (manual)."
    )
    st.sidebar.data_editor(
        _phases_df_for_sidebar(),
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="bl_phases_editor",
        column_config={
            "label": st.column_config.TextColumn("Label"),
            "year_start": st.column_config.NumberColumn("Year start", min_value=1, step=1, format="%d"),
            "year_end": st.column_config.NumberColumn("Year end", min_value=1, step=1, format="%d"),
            "monthly_amount": st.column_config.NumberColumn("Monthly amount", min_value=0.0, format="%.2f"),
        },
    )

    st.sidebar.markdown("**Drawdown ladder**")
    st.sidebar.caption(
        "How much to scale the phase base when the benchmark is down from ATH. "
        "Use thresholds ≤ 0 (e.g. -20 = this row applies at −20% drawdown or worse). "
        "Rows need not be sorted; the deepest matching band wins."
    )
    st.sidebar.data_editor(
        _steps_df_for_sidebar(),
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="bl_steps_editor",
        column_config={
            "label": st.column_config.TextColumn("Step label"),
            "drawdown_threshold_pct": st.column_config.NumberColumn("Drawdown ≤ (%)", format="%.2f"),
            "multiplier": st.column_config.NumberColumn("Multiplier (×)", min_value=0.0, format="%.2f"),
        },
    )

    if st.sidebar.button("Save buying ladder settings", type="primary"):
        crash_val = None
        txt = (crash_pct_raw or "").strip()
        if txt:
            try:
                crash_val = float(txt)
            except ValueError:
                st.sidebar.error("Optional crash allocation must be a number or empty.")
                return

        plan_start_clean = plan_start.strip() or None
        phases_df = st.session_state.get("bl_phases_editor")
        if not isinstance(phases_df, pd.DataFrame):
            phases_df = st.session_state.bl_phases_seed
        steps_df = st.session_state.get("bl_steps_editor")
        if not isinstance(steps_df, pd.DataFrame):
            steps_df = st.session_state.bl_steps_seed

        new_settings = BuyingLadderSettings(
            enabled=enabled,
            benchmark_symbol=str(benchmark).strip().upper(),
            phase_selection_mode=mode,
            plan_start_date=plan_start_clean,
            manual_phase_index=int(manual_idx),
            phases=phases_from_rows(phases_df.to_dict("records")),
            ladder_steps=ladder_steps_from_rows(steps_df.to_dict("records")),
            show_calculation_details=show_details,
            crash_extra_equity_pct=crash_val,
            include_buying_ladder_in_weekly_summary=include_weekly,
            suggest_vwce_cndx_split=suggest_split,
        )
        new_settings = merge_with_defaults(new_settings)
        if save_buying_ladder_settings(new_settings):
            st.session_state.bl_phases_seed = _phases_dataframe(new_settings)
            st.session_state.bl_steps_seed = _steps_dataframe(new_settings)
            for widget_key in ("bl_phases_editor", "bl_steps_editor"):
                if widget_key in st.session_state:
                    del st.session_state[widget_key]
            st.sidebar.success("Buying ladder settings saved.")
        else:
            st.sidebar.error("Could not save settings. Check logs and file permissions.")


def render_buying_ladder_card(market_df: pd.DataFrame) -> None:
    """Supplementary dashboard card; no effect on portfolio or alerts."""
    settings = merge_with_defaults(load_buying_ladder_settings())
    result = compute_buying_ladder(settings, market_df)

    if not result.feature_enabled:
        return

    st.subheader("Buying ladder (insight)")
    st.markdown(f"**Benchmark:** {result.benchmark_symbol}")

    c1, c2 = st.columns(2)
    dd_text = "N/A" if result.drawdown_pct is None else f"{result.drawdown_pct:.2f}%"
    c1.metric("Drawdown from ATH", dd_text)
    c2.metric("Active ladder step", result.ladder_step_label)

    c3, c4 = st.columns(2)
    c3.metric("Active phase", result.phase_label)
    c4.metric("Multiplier", f"{result.multiplier:.2f}×")

    c5, c6, c7 = st.columns(3)
    c5.metric("Base monthly", f"{result.base_monthly:,.2f}")
    c6.metric("Recommended this month", f"{result.recommended_monthly:,.2f}")
    extra = result.extra_vs_base
    c7.metric("Extra this month vs base", f"{extra:+,.2f}")

    split = compute_vwce_cndx_split(settings, result, market_df)
    if split is not None and split.show_ui_block:
        st.markdown("**Suggested monthly split (VWCE vs CNDX)**")
        st.caption(
            "The **recommended total** above comes from the buying ladder; this row only suggests how to **split** "
            "that total between VWCE and CNDX."
        )
        g1, g2, g3 = st.columns(3)
        g1.metric("VWCE drawdown (ATH)", _fmt_pct(split.vwce_drawdown_pct))
        g2.metric("CNDX drawdown (ATH)", _fmt_pct(split.cndx_drawdown_pct))
        g3.metric("Relative gap", _fmt_gap(split.relative_gap_pct))
        st.caption(f"Regime by VWCE: **{split.regime_label.replace('_', ' ')}**")
        h1, h2, h3 = st.columns(3)
        h1.metric("Suggested allocation", split.allocation_label)
        h2.metric("VWCE amount (this month)", f"{split.vwce_amount:,.2f}")
        h3.metric("CNDX amount (this month)", f"{split.cndx_amount:,.2f}")

    if settings.show_calculation_details:
        with st.expander("Why this number?", expanded=False):
            for line in result.explanation_lines:
                st.markdown(f"- {line}")
            if split is not None and split.show_ui_block:
                st.markdown("**Allocation**")
                for line in split.explanation_lines:
                    st.markdown(f"- {line}")
    else:
        hint = result.explanation_lines[-1] if result.explanation_lines else ""
        if hint:
            st.caption(hint)
