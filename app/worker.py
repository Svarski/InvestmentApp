"""Standalone alert worker process (UI-independent)."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import json
import logging
import os
import random
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from alerts import AlertEngine, AlertNotifier, AlertSettings, AlertState
from alerts.models import Alert
from alerts.settings_loader import get_alert_settings
from config import DEFAULT_LOOKBACK_PERIOD
import db
from buying_ladder.weekly_appendix import build_buying_ladder_weekly_appendix
from services.reports.weekly_digest import (
    WeeklyDigestState,
    build_daily_digest_message,
    build_weekly_digest_html,
    mark_daily_digest_sent,
    mark_weekly_digest_sent,
    should_send_daily_digest,
    should_send_weekly_digest,
    update_weekly_digest_state,
)
from services.ibkr_client import IBKRClient
from services.portfolio_sync import run_portfolio_sync
from services.market_data import build_market_overview, fetch_history_for_ticker_uncached

logger = logging.getLogger("investment_worker")

@dataclass(frozen=True)
class WorkerConfig:
    """Runtime configuration for the background alert worker."""

    interval_seconds: int = 300
    log_level: str = "INFO"
    run_once: bool = False
    dry_run: bool = False
    lookback_period: str = DEFAULT_LOOKBACK_PERIOD
    portfolio_value: Optional[float] = None
    heartbeat_file: Optional[str] = None
    state_file: str = "./data/alert_state.json"
    sleep_jitter_seconds: int = 10
    fetch_retry_delay_seconds: float = 3.0
    weekly_summary_enabled: bool = False
    weekly_summary_channel: str = "email"
    weekly_summary_day: str = "monday"
    weekly_summary_hour: int = 9
    weekly_summary_timezone: str = "UTC"
    weekly_summary_email_to: Optional[str] = None
    weekly_summary_state_file: str = "./data/weekly_digest_state.json"
    daily_digest_enabled: bool = False
    daily_digest_hour: int = 9
    daily_digest_timezone: str = "UTC"
    ibkr_sync_enabled: bool = False
    ibkr_sync_hour: int = 18


@dataclass(frozen=True)
class CycleResult:
    """One worker cycle outcome used for persistence and weekly digest decisions."""

    success: bool
    alerts: List[object]
    market_df: object
    portfolio_drop_pct: Optional[float]
    portfolio_value: Optional[float]
    portfolio_source: str = "Fallback"


IBKR_PORTFOLIO_MAX_AGE_SEC = 120.0


def _ibkr_payload_timestamp(data: dict) -> Optional[float]:
    """Epoch seconds from IBKR payload, or None if missing/invalid."""
    ts = data.get("timestamp")
    if ts is None:
        return None
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_worker_config_from_env() -> WorkerConfig:
    """Build worker config from environment variables."""
    interval_raw = os.getenv("WORKER_INTERVAL_SECONDS", "300")
    try:
        interval = max(1, int(interval_raw))
    except ValueError:
        interval = 300

    jitter_raw = os.getenv("WORKER_SLEEP_JITTER_SECONDS", "10")
    retry_delay_raw = os.getenv("WORKER_FETCH_RETRY_DELAY_SECONDS", "3.0")
    try:
        jitter_seconds = max(0, int(jitter_raw))
    except ValueError:
        jitter_seconds = 10
    try:
        fetch_retry_delay_seconds = max(0.0, float(retry_delay_raw))
    except ValueError:
        fetch_retry_delay_seconds = 3.0

    weekly_hour_raw = os.getenv("WEEKLY_SUMMARY_HOUR", "9")
    try:
        weekly_hour = min(23, max(0, int(weekly_hour_raw)))
    except ValueError:
        weekly_hour = 9
    weekly_channel = os.getenv("WEEKLY_SUMMARY_CHANNEL", "email").strip().lower()
    weekly_channel = weekly_channel if weekly_channel in {"email", "none"} else "email"
    daily_hour_raw = os.getenv("DAILY_DIGEST_HOUR", "9")
    try:
        daily_hour = min(23, max(0, int(daily_hour_raw)))
    except ValueError:
        daily_hour = 9
    ibkr_sync_hour_raw = os.getenv("IBKR_SYNC_HOUR", "18")
    try:
        ibkr_sync_hour = min(23, max(0, int(ibkr_sync_hour_raw)))
    except ValueError:
        ibkr_sync_hour = 18

    return WorkerConfig(
        interval_seconds=interval,
        log_level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
        run_once=_parse_bool(os.getenv("WORKER_RUN_ONCE"), default=False),
        dry_run=_parse_bool(os.getenv("WORKER_DRY_RUN"), default=False),
        lookback_period=os.getenv("WORKER_LOOKBACK_PERIOD", DEFAULT_LOOKBACK_PERIOD),
        portfolio_value=_parse_float(os.getenv("WORKER_PORTFOLIO_VALUE")),
        heartbeat_file=os.getenv("WORKER_HEARTBEAT_FILE"),
        state_file=os.getenv("ALERT_STATE_FILE", "./data/alert_state.json"),
        sleep_jitter_seconds=jitter_seconds,
        fetch_retry_delay_seconds=fetch_retry_delay_seconds,
        weekly_summary_enabled=_parse_bool(os.getenv("WEEKLY_SUMMARY_ENABLED"), default=False),
        weekly_summary_channel=weekly_channel,
        weekly_summary_day=os.getenv("WEEKLY_SUMMARY_DAY", "monday"),
        weekly_summary_hour=weekly_hour,
        weekly_summary_timezone=os.getenv("WEEKLY_SUMMARY_TIMEZONE", "UTC"),
        weekly_summary_email_to=os.getenv("WEEKLY_SUMMARY_EMAIL_TO"),
        weekly_summary_state_file=os.getenv("WEEKLY_SUMMARY_STATE_FILE", "./data/weekly_digest_state.json"),
        daily_digest_enabled=_parse_bool(os.getenv("DAILY_DIGEST_ENABLED"), default=False),
        daily_digest_hour=daily_hour,
        daily_digest_timezone=os.getenv("DAILY_DIGEST_TIMEZONE", "UTC"),
        ibkr_sync_enabled=_parse_bool(os.getenv("IBKR_SYNC_ENABLED"), default=False),
        ibkr_sync_hour=ibkr_sync_hour,
    )


def load_alert_settings_from_env() -> AlertSettings:
    """Backward-compatible wrapper around the shared loader."""
    return get_alert_settings()


def configure_logging(log_level: str) -> None:
    """Configure Docker-friendly logging to stdout/stderr."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        force=True,
    )


def _symbol_and_level_from_alert_id(alert_id: str) -> tuple[str, float]:
    """Derive DB symbol and numeric level from engine alert id ``{key}:{level:.2f}``."""
    try:
        if not alert_id or ":" not in alert_id:
            return "UNKNOWN", 0.0
        key, level_s = alert_id.rsplit(":", 1)
        level = float(level_s)
        if key.endswith("_drawdown"):
            symbol = key[: -len("_drawdown")] or "UNKNOWN"
        elif key == "portfolio_drop":
            symbol = "PORTFOLIO"
        elif key == "vix_spike":
            symbol = "VIX"
        else:
            symbol = key if key else "UNKNOWN"
        return symbol, level
    except (TypeError, ValueError, AttributeError):
        return "UNKNOWN", 0.0


def _symbol_and_level_for_db(alert: Alert) -> tuple[str, float]:
    """Prefer structured fields on ``Alert`` when present; otherwise parse ``alert.id``."""
    sym = getattr(alert, "symbol", None)
    lev = getattr(alert, "level", None)
    if isinstance(sym, str) and sym.strip() and lev is not None:
        try:
            return sym.strip(), float(lev)
        except (TypeError, ValueError):
            pass
    return _symbol_and_level_from_alert_id(alert.id)


def _is_recent_duplicate_alert(symbol: str, alert_type: str, level: float) -> bool:
    """True if an equivalent row exists in the last 5 minutes (UTC)."""
    conn = None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        conn = db.get_connection()
        row = conn.execute(
            """
            SELECT 1 FROM alerts
            WHERE symbol = ?
              AND type = ?
              AND level = ?
              AND timestamp >= ?
            LIMIT 1
            """,
            (symbol, alert_type, level, cutoff),
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _persist_alerts_to_db(alerts: List[Alert]) -> None:
    for alert in alerts:
        try:
            symbol, level = _symbol_and_level_for_db(alert)
            if _is_recent_duplicate_alert(symbol, alert.type, level):
                logger.debug("Skipping duplicate alert DB insert")
                continue
            ts = datetime.now(timezone.utc).isoformat()
            db.insert_alert(
                timestamp=ts,
                symbol=symbol,
                alert_type=alert.type,
                level=level,
                message=alert.message,
            )
        except Exception as e:
            logger.error("DB insert failed: %s", e)


def write_heartbeat(
    path: Optional[str],
    *,
    success: bool,
    portfolio_source: Optional[str] = None,
    portfolio_ibkr_timestamp: Optional[float] = None,
) -> None:
    """Write heartbeat JSON with last success/failure timestamps and optional portfolio source."""
    if not path:
        return
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        payload: dict = {
            "last_success_timestamp": None,
            "last_failure_timestamp": None,
            "portfolio_source": "Fallback",
            "portfolio_ibkr_timestamp": None,
        }

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    for key in payload:
                        if key in existing:
                            payload[key] = existing[key]
            except Exception:
                logger.warning("Heartbeat file exists but could not be read. path=%s", path)

        if success:
            payload["last_success_timestamp"] = timestamp
            if portfolio_source is not None:
                payload["portfolio_source"] = portfolio_source
                payload["portfolio_ibkr_timestamp"] = portfolio_ibkr_timestamp
        else:
            payload["last_failure_timestamp"] = timestamp

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        logger.exception("Failed to write heartbeat file path=%s", path)


def run_cycle(
    engine: AlertEngine,
    notifier: AlertNotifier,
    config: WorkerConfig,
    ibkr_client: IBKRClient,
) -> CycleResult:
    """Run one worker cycle safely: fetch, evaluate, notify, log."""
    logger.info("Cycle started.")

    market_df, fetch_messages = build_market_overview(
        period=config.lookback_period,
        history_fetcher=fetch_history_for_ticker_uncached,
    )
    if market_df.empty:
        logger.warning(
            "Market dataframe empty on first attempt. Retrying once in %.1fs...",
            config.fetch_retry_delay_seconds,
        )
        if config.fetch_retry_delay_seconds > 0:
            time.sleep(config.fetch_retry_delay_seconds)
        market_df, fetch_messages = build_market_overview(
            period=config.lookback_period,
            history_fetcher=fetch_history_for_ticker_uncached,
        )

    logger.info(
        "Market data fetched. rows=%s warnings=%s",
        len(market_df.index),
        len(fetch_messages),
    )
    for message in fetch_messages:
        logger.warning("Market data warning: %s", message)

    if market_df.empty:
        logger.warning("Market dataframe is empty. Skipping alert evaluation for this cycle.")
        return CycleResult(
            success=False,
            alerts=[],
            market_df=market_df,
            portfolio_drop_pct=None,
            portfolio_value=None,
            portfolio_source="Fallback",
        )

    try:
        ibkr_data = ibkr_client.get_portfolio()
    except Exception:
        ibkr_data = None

    portfolio_value: Optional[float]
    portfolio_source: str
    portfolio_ibkr_timestamp: Optional[float] = None

    if ibkr_data is None:
        portfolio_value = config.portfolio_value
        portfolio_source = "Fallback"
    else:
        ts_f = _ibkr_payload_timestamp(ibkr_data)
        if ts_f is None:
            portfolio_value = config.portfolio_value
            portfolio_source = "Fallback"
        elif (time.time() - ts_f) > IBKR_PORTFOLIO_MAX_AGE_SEC:
            portfolio_value = config.portfolio_value
            portfolio_source = "IBKR_STALE"
            portfolio_ibkr_timestamp = ts_f
        else:
            try:
                portfolio_value = float(ibkr_data["total_value"])
                portfolio_source = "IBKR"
                portfolio_ibkr_timestamp = ts_f
            except (KeyError, TypeError, ValueError):
                portfolio_value = config.portfolio_value
                portfolio_source = "Fallback"

    if portfolio_source == "IBKR":
        logger.info("Portfolio source: IBKR")
    elif portfolio_source == "IBKR_STALE":
        logger.info("Portfolio source: IBKR (stale)")
    else:
        logger.info("Portfolio source: fallback")

    if portfolio_value is None:
        logger.info("Portfolio value not configured for worker; portfolio drop alerts may not trigger.")

    alerts = engine.evaluate(market_df=market_df, portfolio_value=portfolio_value)
    portfolio_drop_pct = _calculate_portfolio_drop_pct(engine=engine, portfolio_value=portfolio_value)
    breakdown = {"drawdown": 0, "portfolio": 0, "vix": 0, "other": 0}
    for alert in alerts:
        if alert.type == "market_drawdown":
            breakdown["drawdown"] += 1
        elif alert.type == "portfolio_drop":
            breakdown["portfolio"] += 1
        elif alert.type == "vix_spike":
            breakdown["vix"] += 1
        else:
            breakdown["other"] += 1

    logger.info(
        "Alert evaluation completed. generated_alerts=%s breakdown=%s",
        len(alerts),
        breakdown,
    )

    _persist_alerts_to_db(alerts)

    if not alerts:
        write_heartbeat(
            config.heartbeat_file,
            success=True,
            portfolio_source=portfolio_source,
            portfolio_ibkr_timestamp=portfolio_ibkr_timestamp,
        )
        return CycleResult(
            success=True,
            alerts=[],
            market_df=market_df,
            portfolio_drop_pct=portfolio_drop_pct,
            portfolio_value=portfolio_value,
            portfolio_source=portfolio_source,
        )

    if config.dry_run:
        logger.info("Dry run enabled. Skipping notification send for %s alerts.", len(alerts))
        logger.info(
            "Notification metrics. attempted_alerts=%s sent_alerts=%s failed_alerts=%s",
            len(alerts),
            0,
            len(alerts),
        )
        write_heartbeat(
            config.heartbeat_file,
            success=True,
            portfolio_source=portfolio_source,
            portfolio_ibkr_timestamp=portfolio_ibkr_timestamp,
        )
        return CycleResult(
            success=True,
            alerts=alerts,
            market_df=market_df,
            portfolio_drop_pct=portfolio_drop_pct,
            portfolio_value=portfolio_value,
            portfolio_source=portfolio_source,
        )

    stats = notifier.send_alerts_with_stats(alerts)
    failed_alerts = max(0, stats.attempted_alerts - stats.sent_alerts)
    logger.info(
        "Notification dispatch completed. attempted_alerts=%s sent_alerts=%s failed_alerts=%s",
        stats.attempted_alerts,
        stats.sent_alerts,
        failed_alerts,
    )
    write_heartbeat(
        config.heartbeat_file,
        success=True,
        portfolio_source=portfolio_source,
        portfolio_ibkr_timestamp=portfolio_ibkr_timestamp,
    )
    return CycleResult(
        success=True,
        alerts=alerts,
        market_df=market_df,
        portfolio_drop_pct=portfolio_drop_pct,
        portfolio_value=portfolio_value,
        portfolio_source=portfolio_source,
    )


def sleep_with_logging(stop_event: threading.Event, seconds: int, jitter_seconds: int) -> None:
    """Sleep until timeout or shutdown event, with fast interruption support."""
    jitter = random.randint(-jitter_seconds, jitter_seconds) if jitter_seconds > 0 else 0
    sleep_for = max(1, seconds + jitter)
    logger.info(
        "Sleeping for %s seconds before next cycle (base=%s jitter=%+s).",
        sleep_for,
        seconds,
        jitter,
    )
    stop_event.wait(timeout=sleep_for)


def run_worker(config: WorkerConfig, alert_settings: AlertSettings) -> None:
    """Run the worker main loop until stopped."""
    stop_event = threading.Event()

    def _handle_shutdown(signum: int, _frame) -> None:
        logger.info("Received shutdown signal=%s. Stopping worker...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    loaded_state = AlertState.load_from_file(config.state_file)
    weekly_digest_state = WeeklyDigestState.load_from_file(config.weekly_summary_state_file)
    engine = AlertEngine(settings=alert_settings, state=loaded_state)
    notifier = AlertNotifier(settings=alert_settings)
    logger.info("Using AlertNotifier facade backed by MultiNotifier.")

    logger.info(
        "Worker started. interval=%ss run_once=%s dry_run=%s channel=%s",
        config.interval_seconds,
        config.run_once,
        config.dry_run,
        alert_settings.channel,
    )
    logger.info("Alert state file path=%s", config.state_file)
    logger.info(
        "Weekly digest config: enabled=%s channel=%s day=%s hour=%s tz=%s state_file=%s",
        config.weekly_summary_enabled,
        config.weekly_summary_channel,
        config.weekly_summary_day,
        config.weekly_summary_hour,
        config.weekly_summary_timezone,
        config.weekly_summary_state_file,
    )
    logger.info(
        "Daily digest config: enabled=%s hour=%s tz=%s",
        config.daily_digest_enabled,
        config.daily_digest_hour,
        config.daily_digest_timezone,
    )
    logger.info(
        "IBKR sync config: enabled=%s hour=%s",
        config.ibkr_sync_enabled,
        config.ibkr_sync_hour,
    )

    try:
        db.init_db()
    except Exception as e:
        logger.error("DB init failed: %s", e)

    ibkr_client = IBKRClient()
    try:
        while not stop_event.is_set():
            logger.info("Beginning worker cycle.")
            cycle_result: Optional[CycleResult] = None
            try:
                cycle_result = run_cycle(
                    engine=engine,
                    notifier=notifier,
                    config=config,
                    ibkr_client=ibkr_client,
                )
                if cycle_result.success:
                    engine.state.save_to_file(config.state_file)
                    weekly_digest_state = update_weekly_digest_state(
                        weekly_digest_state,
                        market_df=cycle_result.market_df,
                        alerts=cycle_result.alerts,
                        portfolio_drop_pct=cycle_result.portfolio_drop_pct,
                        timezone_name=config.weekly_summary_timezone,
                    )
                    weekly_digest_state.save_to_file(config.weekly_summary_state_file)
                    _run_weekly_digest_if_due(
                        config=config,
                        notifier=notifier,
                        weekly_digest_state=weekly_digest_state,
                        market_df=cycle_result.market_df,
                        portfolio_value=cycle_result.portfolio_value
                        if cycle_result.portfolio_value is not None
                        else config.portfolio_value,
                        portfolio_drop_pct=cycle_result.portfolio_drop_pct,
                    )
                    _run_daily_digest_if_due(
                        config=config,
                        notifier=notifier,
                        weekly_digest_state=weekly_digest_state,
                        market_df=cycle_result.market_df,
                    )
                    if config.ibkr_sync_enabled:
                        try:
                            current_hour = datetime.now(timezone.utc).hour
                            if current_hour >= config.ibkr_sync_hour:
                                run_portfolio_sync()
                        except Exception:
                            logger.exception("IBKR daily sync failed.")
                else:
                    write_heartbeat(config.heartbeat_file, success=False)
            except Exception:
                logger.exception("Worker cycle failed unexpectedly.")
                write_heartbeat(config.heartbeat_file, success=False)
            finally:
                try:
                    ts = datetime.now(timezone.utc).isoformat()
                    pv = config.portfolio_value
                    if cycle_result is not None and cycle_result.portfolio_value is not None:
                        pv = cycle_result.portfolio_value
                    total = float(pv) if pv is not None else 0.0
                    db.insert_portfolio_snapshot(
                        timestamp=ts,
                        total_value=total,
                        vwce_value=0.0,
                        cndx_value=0.0,
                        cash=0.0,
                    )
                except Exception as e:
                    logger.error("DB insert failed: %s", e)

            if config.run_once:
                logger.info("Run-once mode enabled. Exiting after one cycle.")
                break

            sleep_with_logging(
                stop_event=stop_event,
                seconds=config.interval_seconds,
                jitter_seconds=config.sleep_jitter_seconds,
            )
    finally:
        ibkr_client.disconnect()

    logger.info("Worker stopped cleanly.")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for worker entrypoint."""
    parser = argparse.ArgumentParser(description="Run background alert worker.")
    parser.add_argument("--run-once", action="store_true", help="Run a single cycle and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    worker_config = load_worker_config_from_env()
    if args.run_once:
        worker_config = WorkerConfig(
            interval_seconds=worker_config.interval_seconds,
            log_level=worker_config.log_level,
            run_once=True,
            dry_run=worker_config.dry_run,
            lookback_period=worker_config.lookback_period,
            portfolio_value=worker_config.portfolio_value,
            heartbeat_file=worker_config.heartbeat_file,
            state_file=worker_config.state_file,
            sleep_jitter_seconds=worker_config.sleep_jitter_seconds,
            fetch_retry_delay_seconds=worker_config.fetch_retry_delay_seconds,
            weekly_summary_enabled=worker_config.weekly_summary_enabled,
            weekly_summary_channel=worker_config.weekly_summary_channel,
            weekly_summary_day=worker_config.weekly_summary_day,
            weekly_summary_hour=worker_config.weekly_summary_hour,
            weekly_summary_timezone=worker_config.weekly_summary_timezone,
            weekly_summary_email_to=worker_config.weekly_summary_email_to,
            weekly_summary_state_file=worker_config.weekly_summary_state_file,
            daily_digest_enabled=worker_config.daily_digest_enabled,
            daily_digest_hour=worker_config.daily_digest_hour,
            daily_digest_timezone=worker_config.daily_digest_timezone,
            ibkr_sync_enabled=worker_config.ibkr_sync_enabled,
            ibkr_sync_hour=worker_config.ibkr_sync_hour,
        )

    configure_logging(worker_config.log_level)
    alert_settings = load_alert_settings_from_env()
    logger.info(
        "Effective startup config: alert_channel=%s weekly_summary_enabled=%s weekly_summary_channel=%s daily_digest_enabled=%s email_to_set=%s telegram_chat_id_set=%s",
        alert_settings.channel,
        worker_config.weekly_summary_enabled,
        worker_config.weekly_summary_channel,
        worker_config.daily_digest_enabled,
        bool(alert_settings.email_to),
        bool(alert_settings.telegram_chat_id),
    )
    run_worker(config=worker_config, alert_settings=alert_settings)


def _calculate_portfolio_drop_pct(engine: AlertEngine, portfolio_value: Optional[float]) -> Optional[float]:
    if portfolio_value is None:
        return None
    peak = engine.state.get_metric("portfolio_peak_value")
    if peak in (None, 0):
        return None
    return ((portfolio_value - peak) / peak) * 100.0


def _run_weekly_digest_if_due(
    *,
    config: WorkerConfig,
    notifier: AlertNotifier,
    weekly_digest_state: WeeklyDigestState,
    market_df,
    portfolio_value: Optional[float],
    portfolio_drop_pct: Optional[float],
) -> None:
    if config.weekly_summary_channel == "none":
        logger.info("Weekly digest skipped due to weekly channel setting: channel=none")
        return

    if not should_send_weekly_digest(
        weekly_digest_state,
        enabled=config.weekly_summary_enabled,
        day=config.weekly_summary_day,
        hour=config.weekly_summary_hour,
        timezone_name=config.weekly_summary_timezone,
    ):
        logger.info("Weekly digest skipped (not due).")
        return
    logger.info("Weekly digest due for current schedule.")

    recipient = config.weekly_summary_email_to or notifier.settings.email_to
    if not recipient:
        logger.warning("Weekly digest due but no recipient configured.")
        return

    subject = "Weekly Investment System Digest"
    bl_appendix, bl_reason = build_buying_ladder_weekly_appendix(market_df)
    logger.info("Weekly buying ladder appendix: reason=%s has_body=%s", bl_reason, bool(bl_appendix))

    html_body = build_weekly_digest_html(
        state=weekly_digest_state,
        market_df=market_df,
        portfolio_value=portfolio_value,
        portfolio_drop_pct=portfolio_drop_pct,
        buying_ladder_appendix=bl_appendix,
    )
    body = (
        "Weekly Investment Summary\n\n"
        "This message is optimized for HTML-capable email clients.\n"
        "If you cannot view HTML, please switch to an email client that supports rich content."
    )

    sent = notifier.send_weekly_summary_email(
        subject=subject,
        body=body,
        recipient=recipient,
        html_body=html_body,
    )
    if not sent:
        logger.warning("Weekly digest send failed.")
        return

    mark_weekly_digest_sent(
        weekly_digest_state,
        timezone_name=config.weekly_summary_timezone,
    )
    weekly_digest_state.save_to_file(config.weekly_summary_state_file)
    logger.info("Weekly digest sent successfully to %s", recipient)


def _run_daily_digest_if_due(
    *,
    config: WorkerConfig,
    notifier: AlertNotifier,
    weekly_digest_state: WeeklyDigestState,
    market_df,
) -> None:
    if not should_send_daily_digest(
        weekly_digest_state,
        enabled=config.daily_digest_enabled,
        hour=config.daily_digest_hour,
        timezone_name=config.daily_digest_timezone,
    ):
        return

    if market_df is None or market_df.empty:
        logger.info("Daily digest skipped due to missing market data.")
        return

    if notifier.settings.channel not in {"telegram", "both"}:
        logger.info("Daily digest skipped because Telegram channel is disabled.")
        return

    if not notifier.settings.telegram_bot_token or not notifier.settings.telegram_chat_id:
        logger.info("Daily digest skipped because Telegram credentials are not configured.")
        return

    try:
        message = build_daily_digest_message(market_df=market_df, state=weekly_digest_state)
        sent = notifier.send_telegram(message)
        if not sent:
            logger.info("Daily digest skipped/failed on Telegram send path.")
            return
        mark_daily_digest_sent(weekly_digest_state, timezone_name=config.daily_digest_timezone)
        weekly_digest_state.save_to_file(config.weekly_summary_state_file)
        logger.info("Daily mini digest sent on Telegram.")
    except Exception:
        logger.exception("Daily digest send failed.")


if __name__ == "__main__":
    main()
