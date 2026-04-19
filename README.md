# Personal Investment Dashboard MVP

--Nova verzija:
--comp
git add .
git commit -m "update"
git push

--server
ssh root@178.104.129.99
cd ~/investmentapp
git reset --hard
git pull
docker compose down
docker compose up -d --build auth

Simple Streamlit MVP for tracking core market instruments and a manually entered portfolio.

## Features

- Market overview for `VWCE`, `CNDX`, `SPY`, `VIX`, `DXY`, `TNX`
- Latest price, daily % change, and drawdown from all-time high
- Manual portfolio input (ticker, quantity, average buy price)
- Portfolio calculations: current value, cost basis, unrealized PnL, unrealized PnL %
- Instrument price chart with selectable time range
- Graceful handling of missing/failed ticker data

## Project Structure

```text
.
├── main.py
├── config.py
├── requirements.txt
├── README.md
├── app
│   ├── ui.py
│   └── components.py
├── services
│   └── market_data.py
└── logic
    └── calculations.py
```

## Setup

1. Create and activate a virtual environment (recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

### Optional: `.env` for the worker

Copy the example file and edit values (secrets stay out of the shell):

```bash
copy .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

- Variables already set in your environment take precedence over `.env` (no override).
- If `.env` is missing, the worker still runs; use manual env vars or defaults as before.

The worker loads `.env` from the project root automatically when you run `python -m app.worker`.

## Run

```bash
streamlit run main.py
```

Then open the local URL shown in the terminal (usually `http://localhost:8501`).

### Optional: FastAPI login in front of Streamlit

To replace Nginx Basic Auth with a real HTML login form, run Streamlit on `127.0.0.1:8501` and start the auth gateway (separate process) on port `8000`:

```bash
set APP_USERNAME=your_user
set APP_PASSWORD=your_secret
set AUTH_COOKIE_SECURE=false
uvicorn auth_server:app --host 0.0.0.0 --port 8000
```

On Linux/macOS use `export` instead of `set`. Point Nginx at this gateway (`proxy_pass` to `http://127.0.0.1:8000`), not directly at Streamlit. Environment variables:

- `APP_USERNAME`, `APP_PASSWORD` (required for login)
- `STREAMLIT_ORIGIN` (default `http://127.0.0.1:8501`)
- `AUTH_COOKIE_SECURE=true` when the site is served over HTTPS
- Optional: `AUTH_SESSION_COOKIE`, `AUTH_SESSION_VALUE`, `AUTH_SESSION_MAX_AGE`

Logout: open `/logout` in the browser.

**Nginx:** Change the `location /` block that currently proxies to port `8501` so it proxies to port `8000` (the FastAPI app). Keep WebSocket upgrade headers (`Upgrade`, `Connection`) as you already do for Streamlit; the gateway forwards WS to Streamlit.

In Docker Compose, set `STREAMLIT_ORIGIN` to the Streamlit service URL reachable from the auth container (for example `http://streamlit:8501`) and expose the auth process on the port Nginx uses.

## Run background worker

The background worker evaluates alerts independently from the UI.

With a project-root `.env` file (see above), you do not need to export variables in PowerShell.

```bash
python -m app.worker
```

Run one cycle only (for testing):

```bash
python -m app.worker --run-once
```

Example environment variables:

- `WORKER_INTERVAL_SECONDS=300`
- `WORKER_LOG_LEVEL=INFO`
- `WORKER_DRY_RUN=true`
- `ALERT_CHANNEL=telegram` (`none`, `telegram`, `email`, `both`)
- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_CHAT_ID=...`

## Configuration

Main settings are in `config.py`:

- `TRACKED_INSTRUMENTS`: tracked symbols/tickers and display names
- `DEFAULT_LOOKBACK_PERIOD`: default period used for market overview
- `DEFAULT_CHART_PERIOD`: default period for chart section
- `CHART_PERIOD_OPTIONS`: selectable chart ranges
- `CACHE_TTL_SECONDS`: market data cache duration

## Notes

- Data comes from Yahoo Finance through `yfinance`; values may be delayed.
- Portfolio data is in-memory for MVP usage and is not persisted.
