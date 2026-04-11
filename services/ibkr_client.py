"""
Read-only Interactive Brokers portfolio client via ib_insync.

Connection settings: IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID (see IBKRClient.__init__).
"""

from __future__ import annotations

import copy
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ib_insync import IB

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 60.0
_CONNECT_TIMEOUT_SEC = 15.0


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.debug("Invalid %s=%r; using default %s", key, raw, default)
        return default


def _safe_float(value: object) -> float:
    """Parse IB numeric fields (account summary, positions) without raising."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _position_qty_avg(position: Any) -> tuple[float, float]:
    """quantity and avg_cost as floats; never raises."""
    try:
        qty = float(position.position)
    except (TypeError, ValueError, AttributeError):
        qty = 0.0
    try:
        avg_cost = float(position.avgCost)
    except (TypeError, ValueError, AttributeError):
        avg_cost = 0.0
    return qty, avg_cost


def _account_tag_value(rows: List[Any], tag: str) -> float:
    """Pick NetLiquidation / TotalCashValue-style row; prefer BASE then USD."""
    matches = [r for r in rows if getattr(r, "tag", None) == tag]
    if not matches:
        return 0.0
    for currency in ("BASE", "USD"):
        for r in matches:
            if getattr(r, "currency", None) == currency:
                return _safe_float(r.value)
    return _safe_float(matches[0].value)


def _position_market_value(position: Any) -> float:
    if getattr(position, "marketValue", None) is not None:
        return _safe_float(getattr(position, "marketValue"))
    qty, avg = _position_qty_avg(position)
    return qty * avg


class IBKRClient:
    """Minimal IBKR data provider: connect, fetch portfolio snapshot, disconnect."""

    def __init__(self) -> None:
        self.host = os.getenv("IBKR_HOST", "127.0.0.1") or "127.0.0.1"
        self.port = _env_int("IBKR_PORT", 7497)
        self.client_id = _env_int("IBKR_CLIENT_ID", 1)

        self.ib = IB()
        self.connected = False

        self._cache_payload: Optional[Dict[str, Any]] = None
        self._cache_mono: float = 0.0

    def connect(self) -> None:
        if self.connected:
            return
        try:
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=_CONNECT_TIMEOUT_SEC,
                readonly=True,
            )
            self.connected = True
            logger.info(
                "IBKR connected to %s:%s (clientId=%s)",
                self.host,
                self.port,
                self.client_id,
            )
        except Exception as exc:
            self.connected = False
            logger.error(
                "IBKR connection failed (host=%s port=%s clientId=%s): %s",
                self.host,
                self.port,
                self.client_id,
                exc,
            )

    def disconnect(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception as exc:
            logger.debug("IBKR disconnect error: %s", exc)
        finally:
            self.connected = False
            self._cache_payload = None
            self._cache_mono = 0.0

    def ensure_connection(self) -> bool:
        if self.connected and not self.ib.isConnected():
            self.connected = False
        if not self.connected:
            self.connect()
        return self.connected

    def get_portfolio(self) -> Optional[Dict[str, Any]]:
        """
        Return normalized portfolio dict, or None on connection/errors.

        Shape:
          total_value, cash (floats), positions (list of dicts with
          symbol, quantity, market_value, avg_cost), source 'ibkr',
          timestamp (epoch seconds from time.time() when fetched).
        """
        now = time.monotonic()
        if (
            self._cache_payload is not None
            and (now - self._cache_mono) < _CACHE_TTL_SEC
        ):
            return copy.deepcopy(self._cache_payload)

        try:
            if not self.ensure_connection():
                return None

            # Triggers reqAccountSummary on first use inside ib_insync; bounded by IB timeouts.
            summary_rows = self.ib.accountSummary()
            total_value = _account_tag_value(summary_rows, "NetLiquidation")
            cash = _account_tag_value(summary_rows, "TotalCashValue")

            raw_positions = self.ib.positions()
            positions_out: List[Dict[str, Any]] = []
            for p in raw_positions:
                contract = getattr(p, "contract", None)
                symbol = getattr(contract, "symbol", "") if contract else ""
                qty, avg_cost = _position_qty_avg(p)
                mval = _position_market_value(p)
                positions_out.append(
                    {
                        "symbol": symbol,
                        "quantity": qty,
                        "market_value": mval,
                        "avg_cost": avg_cost,
                    }
                )

            payload: Dict[str, Any] = {
                "total_value": total_value,
                "cash": cash,
                "positions": positions_out,
                "source": "ibkr",
                "timestamp": time.time(),
            }
            self._cache_payload = copy.deepcopy(payload)
            self._cache_mono = now
            return payload

        except Exception as exc:
            logger.debug("IBKR get_portfolio failed: %s", exc)
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = IBKRClient()
    try:
        data = client.get_portfolio()
        print(data)
    finally:
        client.disconnect()
