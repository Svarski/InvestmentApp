"""Daily IBKR portfolio sync to SQLite."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import db
from requests.exceptions import ConnectionError as RequestsConnectionError, ReadTimeout
from services.ibkr_flex import fetch_flex_report, parse_flex_report, request_flex_report

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "ibkr_sync_state.json"
_PORTFOLIO_SYNC_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio_sync_state.json"
SYMBOL_MAPPING = {
    "SXRV": "CNDX",
}


def _write_state_atomic(path: Path, data: Dict[str, Any]) -> None:
    path = Path(path)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=str(path.parent)) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, str(path))


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_sync_state() -> Dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    try:
        with _STATE_PATH.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning("Failed to load IBKR sync state: %s", exc)
        return {}


def _save_sync_state(payload: Dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_state_atomic(_STATE_PATH, payload)
    except Exception as exc:
        logger.error("Failed to save IBKR sync state: %s", exc)


def load_portfolio_sync_state() -> Dict[str, Any]:
    """Read sync health state for UI/monitoring with safe defaults."""
    defaults: Dict[str, Any] = {
        "last_successful_sync": None,
        "last_attempt": None,
        "status": "unknown",
        "error": None,
    }
    if not _PORTFOLIO_SYNC_STATE_PATH.exists():
        return defaults
    try:
        with _PORTFOLIO_SYNC_STATE_PATH.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return defaults
        return {
            "last_successful_sync": payload.get("last_successful_sync"),
            "last_attempt": payload.get("last_attempt"),
            "status": str(payload.get("status") or "unknown"),
            "error": payload.get("error"),
        }
    except Exception as exc:
        logger.warning("Failed to load portfolio sync state: %s", exc)
        return defaults


def _save_portfolio_sync_state(payload: Dict[str, Any]) -> None:
    """Persist sync health state atomically; never raises."""
    try:
        _PORTFOLIO_SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_state_atomic(_PORTFOLIO_SYNC_STATE_PATH, payload)
    except Exception as exc:
        logger.error("Failed to save portfolio sync state: %s", exc)


def _update_portfolio_sync_state_success(now_iso: str) -> None:
    _save_portfolio_sync_state(
        {
            "last_successful_sync": now_iso,
            "last_attempt": now_iso,
            "status": "success",
            "error": None,
        }
    )


def _update_portfolio_sync_state_in_progress(now_iso: str) -> None:
    existing = load_portfolio_sync_state()
    _save_portfolio_sync_state(
        {
            "last_successful_sync": existing.get("last_successful_sync"),
            "last_attempt": now_iso,
            "status": "in_progress",
            "error": None,
        }
    )


def _update_portfolio_sync_state_failed(now_iso: str, error_message: str) -> None:
    short_error = (error_message or "Unknown error").strip()
    if len(short_error) > 200:
        short_error = short_error[:197] + "..."
    existing = load_portfolio_sync_state()
    _save_portfolio_sync_state(
        {
            "last_successful_sync": existing.get("last_successful_sync"),
            "last_attempt": now_iso,
            "status": "failed",
            "error": short_error,
        }
    )


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def should_sync_today() -> bool:
    """Return True once per UTC day based on sync state file."""
    try:
        state = _load_sync_state()
        last_sync_date = str(state.get("last_sync_date", "")).strip()
        today_utc = datetime.now(timezone.utc).date().isoformat()
        return last_sync_date != today_utc
    except Exception as exc:
        logger.error("should_sync_today failed: %s", exc)
        # Fail-open to keep sync deterministic and self-healing.
        return True


def calculate_portfolio_summary(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build VWCE/CNDX/cash/total summary and raw positions JSON string."""
    positions: List[Dict[str, Any]] = parsed_data.get("positions", [])
    cash_balances: List[Dict[str, Any]] = parsed_data.get("cash_balances", [])

    vwce_value = 0.0
    cndx_value = 0.0
    for position in positions:
        raw_symbol = position.get("symbol")
        symbol = SYMBOL_MAPPING.get(raw_symbol, raw_symbol)
        market_value = _safe_float(position.get("market_value"))
        if symbol == "VWCE":
            vwce_value += market_value
        elif symbol == "CNDX":
            cndx_value += market_value

    cash = 0.0
    for balance in cash_balances:
        cash += _safe_float(balance.get("balance"))

    total_value = _safe_float(parsed_data.get("net_liquidation_value"))
    if total_value == 0.0:
        total_value = sum(_safe_float(p.get("market_value")) for p in positions) + cash

    return {
        "total_value": total_value,
        "vwce_value": vwce_value,
        "cndx_value": cndx_value,
        "cash": cash,
        "raw_positions": json.dumps(positions, ensure_ascii=True, default=str),
    }


def run_portfolio_sync() -> None:
    """Run one IBKR sync cycle and persist portfolio snapshot safely."""
    try:
        if not should_sync_today():
            logger.info("IBKR sync skipped: already synced today")
            return

        _update_portfolio_sync_state_in_progress(_utc_now_iso_z())
        reference_code = ""
        request_start_ts = time.monotonic()
        request_max_duration_seconds = 120
        request_attempt = 0
        while (time.monotonic() - request_start_ts) < request_max_duration_seconds:
            request_attempt += 1
            try:
                reference_code = request_flex_report()
                elapsed_success = int(time.monotonic() - request_start_ts)
                logger.info("IBKR Flex request succeeded after %ss", elapsed_success)
                break
            except (ReadTimeout, RequestsConnectionError) as exc:
                message = str(exc)
                delay_seconds = request_attempt * 5
                elapsed = int(time.monotonic() - request_start_ts)
                remaining = request_max_duration_seconds - elapsed
                if remaining <= 0:
                    break
                sleep_for = min(delay_seconds, remaining)
                logger.warning(
                    "IBKR Flex request network error. Retrying... attempt=%s delay=%ss error=%s",
                    request_attempt,
                    sleep_for,
                    message,
                )
                time.sleep(sleep_for)
                continue
            except Exception as exc:
                message = str(exc)
                if "[1001]" in message:
                    elapsed = int(time.monotonic() - request_start_ts)
                    remaining = request_max_duration_seconds - elapsed
                    if remaining <= 0:
                        break
                    sleep_for = min(5, remaining)
                    logger.info(
                        "IBKR Flex request not ready (1001). Retrying... elapsed=%ss",
                        elapsed,
                    )
                    time.sleep(sleep_for)
                    continue
                raise
        if not reference_code:
            raise Exception("IBKR Flex request not ready after retry window")
        logger.info("IBKR Flex report requested. reference_code=%s", reference_code)

        raw_xml = ""
        start_ts = time.monotonic()
        max_duration_seconds = 300
        attempt = 0
        last_error_message = None
        logger.info("Starting IBKR Flex polling (max 300s)")
        while (time.monotonic() - start_ts) < max_duration_seconds:
            attempt += 1
            try:
                raw_xml = fetch_flex_report(reference_code)
                total_elapsed = int(time.monotonic() - start_ts)
                logger.info("IBKR Flex report fetched successfully after %ss", total_elapsed)
                break
            except Exception as exc:
                message = str(exc)
                last_error_message = message
                if "[1001]" not in message:
                    raise

                elapsed = int(time.monotonic() - start_ts)
                delay_seconds = min(5 * attempt, 30)
                remaining = max_duration_seconds - elapsed
                if remaining <= 0:
                    break
                sleep_for = min(delay_seconds, remaining)
                logger.info(
                    "IBKR Flex polling... attempt=%s elapsed=%ss next_delay=%ss",
                    attempt,
                    elapsed,
                    sleep_for,
                )
                time.sleep(sleep_for)

        if not raw_xml:
            raise Exception(
                f"IBKR Flex report not ready after polling window (5 minutes). Last error: {last_error_message}"
            )
        parsed_data = parse_flex_report(raw_xml)
        positions = parsed_data.get("positions", [])
        logger.info("IBKR positions count=%d", len(positions))
        summary = calculate_portfolio_summary(parsed_data)

        # Round financial values to 2 decimals to avoid float artifacts
        total_value = round(_safe_float(summary.get("total_value")), 2)
        vwce_value = round(_safe_float(summary.get("vwce_value")), 2)
        cndx_value = round(_safe_float(summary.get("cndx_value")), 2)
        cash = round(_safe_float(summary.get("cash")), 2)
        if total_value <= 0:
            msg = f"invalid total_value={total_value}; snapshot not written"
            logger.error("IBKR sync failed: %s", msg)
            _update_portfolio_sync_state_failed(_utc_now_iso_z(), msg)
            return

        timestamp = datetime.now(timezone.utc).isoformat()
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO portfolio_snapshots
                    (timestamp, total_value, vwce_value, cndx_value, cash, raw_positions, raw_xml)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    total_value,
                    vwce_value,
                    cndx_value,
                    cash,
                    summary["raw_positions"],
                    raw_xml,
                ),
            )
            conn.commit()
            today_utc = datetime.now(timezone.utc).date().isoformat()
            _save_sync_state(
                {
                    "last_sync_date": today_utc,
                    "last_sync_timestamp": timestamp,
                }
            )
            _update_portfolio_sync_state_success(_utc_now_iso_z())
        finally:
            conn.close()

        logger.info(
            "IBKR sync SUCCESS: total=%.2f vwce=%.2f cndx=%.2f cash=%.2f",
            total_value,
            vwce_value,
            cndx_value,
            cash,
        )
    except Exception as exc:
        _update_portfolio_sync_state_failed(_utc_now_iso_z(), str(exc))
        logger.error("IBKR sync failed: %s", exc, exc_info=True)

if __name__ == "__main__":
    run_portfolio_sync()
