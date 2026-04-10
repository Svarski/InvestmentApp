"""Streamlit entrypoint for the personal investment dashboard MVP."""

from __future__ import annotations

import streamlit as st

from app.ui import render_dashboard


def main() -> None:
    st.set_page_config(
        page_title="Investment Dashboard MVP",
        page_icon="📈",
        layout="wide",
    )
    render_dashboard()


if __name__ == "__main__":
    main()
