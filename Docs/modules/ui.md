# UI (Streamlit)

## Role

Displays data to the user.

## Responsibilities

* show market data
* show portfolio (IBKR-synced snapshots only)
* show alerts
* show charts

Manual portfolio input has been removed. Portfolio is now sourced exclusively from IBKR sync (`portfolio_snapshots` via the worker / sync pipeline).

## Rules

* no business logic
* no calculations
* no duplication of backend logic

## Do NOT

* trigger alerts
* compute decisions

---

## Portfolio Overview

The **💼 Portfolio Overview** block (`render_portfolio_overview` in `app/ui.py`) shows the most recent row from the **`portfolio_snapshots`** table (same source as portfolio history charts).

### Data source

* **Table:** `portfolio_snapshots`
* **Row selection:** newest by `timestamp` (`get_latest_portfolio_snapshot()` in `db.py`)

### Fields used

| Field           | Meaning                          |
|----------------|-----------------------------------|
| `total_value`  | Total portfolio value (€)         |
| `vwce_value`   | VWCE position value (€)           |
| `cndx_value`   | CNDX position value (€)           |
| `cash`         | Cash balance (€)                  |

### Calculation logic (display only)

Values are read as stored; no extra business rules.

* `total` = `total_value`
* `vwce` = `vwce_value`
* `cndx` = `cndx_value`
* `cash` = `cash`

Allocation percentages (of total):

* `vwce_pct` = `vwce / total * 100`
* `cndx_pct` = `cndx / total * 100`
* `cash_pct` = `cash / total * 100`

If `total == 0`, all percentages are **0** (no division by zero). Non-finite numeric values are treated as missing and the section shows the empty state.

### Layout (presentation)

* **Heading:** a single `### 💼 Portfolio Overview` (`st.markdown`) at the start of `render_portfolio_overview` — no duplicate title elsewhere in the dashboard.
* **Hero:** **Total Value** is one full-width `st.metric` (no delta); it is visually primary.
* **Sub-metrics:** three columns for **VWCE**, **CNDX**, and **Cash** — each shows € via `st.metric` and allocation **without** `st.metric` `delta` (avoids misleading ↑/↓ cues). The same percentages as above appear as `st.caption`, e.g. `78.4% of portfolio`.
* **Cash:** an extra short caption *Uninvested* under the cash column (copy only; same numbers).
* **Spacing:** `st.write("")` after the hero and after the three-column row for separation from adjacent dashboard content.
