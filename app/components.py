"""Reusable Streamlit UI components."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st


def format_percent(value: Optional[float]) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:.2f}%"


def format_price(value: Optional[float]) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:,.2f}"


def render_info_banner() -> None:
    st.caption("Data source: yfinance. Values may be delayed and for informational use only.")


def render_warning_messages(messages: list[str]) -> None:
    if not messages:
        return
    for message in messages:
        st.warning(message)
