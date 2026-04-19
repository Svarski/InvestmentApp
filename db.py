"""Minimal SQLite persistence for alert history and portfolio snapshots.

Uses sqlite3 only; no ORM. Database file: data/app.db (relative to this package root).

Timestamp columns are ``TEXT``; callers must pass UTC wall time as ISO 8601 strings,
e.g. ``datetime.utcnow().isoformat()`` (this module does not generate timestamps).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Path to the SQLite file (next to project root, not cwd-dependent)
_DB_DIR = Path(__file__).resolve().parent / "data"
_DB_PATH = _DB_DIR / "app.db"

logger = logging.getLogger(__name__)

# --- Schema (explicit DDL) -------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    type TEXT NOT NULL,
    level REAL NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    timestamp TEXT NOT NULL,
    total_value REAL NOT NULL,
    vwce_value REAL NOT NULL,
    cndx_value REAL NOT NULL,
    cash REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol);
CREATE INDEX IF NOT EXISTS idx_portfolio_timestamp ON portfolio_snapshots(timestamp);
"""


def get_connection() -> sqlite3.Connection:
    """Return a sqlite3 connection to data/app.db.

    The caller must close the connection when finished (e.g. ``conn.close()`` or
    a context manager).
    """
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables and indexes if they do not exist."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection | None = None
    try:
        with sqlite3.connect(_DB_PATH, timeout=5) as c:
            conn = c
            c.row_factory = sqlite3.Row
            c.executescript(SCHEMA_SQL)
            try:
                c.execute("ALTER TABLE portfolio_snapshots ADD COLUMN raw_positions TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            try:
                c.execute("ALTER TABLE portfolio_snapshots ADD COLUMN raw_xml TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    finally:
        if conn is not None:
            conn.close()


def insert_alert(
    timestamp: str,
    symbol: str,
    alert_type: str,
    level: float,
    message: str,
) -> int:
    """Insert one alert row; returns the new row id. ``alert_type`` maps to column ``type``.

    ``timestamp`` must already be a UTC ISO 8601 string (e.g.
    ``datetime.utcnow().isoformat()``); this function does not format or infer time.
    """
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection | None = None
    try:
        with sqlite3.connect(_DB_PATH, timeout=5) as c:
            conn = c
            c.row_factory = sqlite3.Row
            cur = c.execute(
                """
                INSERT INTO alerts (timestamp, symbol, type, level, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, symbol, alert_type, level, message),
            )
            lastrowid = int(cur.lastrowid)
    finally:
        if conn is not None:
            conn.close()
    return lastrowid


def insert_portfolio_snapshot(
    timestamp: str,
    total_value: float,
    vwce_value: float,
    cndx_value: float,
    cash: float,
) -> None:
    """Insert one portfolio snapshot row.

    ``timestamp`` must already be a UTC ISO 8601 string (e.g.
    ``datetime.utcnow().isoformat()``); this function does not format or infer time.
    """
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection | None = None
    try:
        with sqlite3.connect(_DB_PATH, timeout=5) as c:
            conn = c
            c.row_factory = sqlite3.Row
            c.execute(
                """
                INSERT INTO portfolio_snapshots
                    (timestamp, total_value, vwce_value, cndx_value, cash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, total_value, vwce_value, cndx_value, cash),
            )
    finally:
        if conn is not None:
            conn.close()


def get_recent_alerts(limit: int = 20) -> pd.DataFrame:
    """Return the latest alerts as a DataFrame (timestamp descending)."""
    conn: sqlite3.Connection | None = None
    try:
        conn = get_connection()
        return pd.read_sql_query(
            """
            SELECT *
            FROM alerts
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            conn,
            params=(limit,),
        )
    except Exception:
        logger.error("DB query failed", exc_info=True)
        return pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()


def get_portfolio_history(days: int = 30) -> pd.DataFrame:
    """Return portfolio snapshots from the last ``days`` days (UTC), oldest first."""
    conn: sqlite3.Connection | None = None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = get_connection()
        return pd.read_sql_query(
            """
            SELECT *
            FROM portfolio_snapshots
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT 5000
            """,
            conn,
            params=(cutoff,),
        )
    except Exception:
        logger.error("DB query failed", exc_info=True)
        return pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()


def get_latest_portfolio_snapshot() -> pd.DataFrame:
    """Return the newest ``portfolio_snapshots`` row (at most one), or empty."""
    conn: sqlite3.Connection | None = None
    try:
        conn = get_connection()
        return pd.read_sql_query(
            """
            SELECT *
            FROM portfolio_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            conn,
        )
    except Exception:
        logger.error("DB query failed", exc_info=True)
        return pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()
