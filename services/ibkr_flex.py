"""IBKR Flex Web Service client.

This module is intentionally isolated:
- requests report generation
- fetches raw XML report
- optional `parse_flex_report()` for structured fields (no DB writes)
"""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv(override=False)

logger = logging.getLogger(__name__)

_BASE_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet"
_SEND_REQUEST_URL = f"{_BASE_URL}/FlexStatementService.SendRequest"
_GET_STATEMENT_URL = f"{_BASE_URL}/FlexStatementService.GetStatement"

_INITIAL_WAIT_SECONDS = 10
_RETRY_DELAYS_SECONDS = [10, 10, 15, 20, 30]  # retry schedule after "not ready" (1019)

_HTTP_HEADERS = {"User-Agent": "investment-app/1.0"}


def _validate_flex_http_body(text: str, context: str) -> None:
    if not text or (("<Flex" not in text) and ("<FlexStatement" not in text)):
        logger.error("%s: invalid IBKR response (empty or not Flex XML)", context)
        raise Exception("Invalid IBKR response (empty or not XML)")


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise Exception(f"Missing required environment variable: {name}")
    return value


def _xml_text(root: ET.Element, tag: str) -> Optional[str]:
    node = root.find(f".//{tag}")
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _attr(elem: Optional[ET.Element], name: str) -> Optional[str]:
    if elem is None:
        return None
    raw = elem.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _to_float(value: Optional[str]) -> float:
    """Parse IBKR numeric strings to float; missing or invalid → 0.0."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return 0.0


def _to_float_optional(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _empty_parsed_result(raw_xml: str) -> Dict[str, Any]:
    return {
        "ibkr_timestamp": None,
        "account_id": None,
        "positions": [],
        "cash_balances": [],
        "net_liquidation_value": 0.0,
        "base_currency": "EUR",
        "raw_xml": raw_xml,
    }


def _first_flex_statement(root: ET.Element) -> Optional[ET.Element]:
    if root.tag == "FlexStatement":
        return root
    for el in root.iter("FlexStatement"):
        return el
    return None


def _extract_net_liquidation(stmt: ET.Element) -> float:
    """EquitySummary BASE_SUMMARY total, else AccountInformation NetLiquidation, else 0.0."""
    for row in stmt.iter("EquitySummaryByReportDateInBase"):
        if _attr(row, "currency") == "BASE_SUMMARY":
            t = _to_float_optional(row.get("total"))
            if t is not None:
                return t
    ai = stmt.find("AccountInformation")
    if ai is not None:
        nl = _to_float_optional(ai.get("NetLiquidation"))
        if nl is not None:
            return nl
    return 0.0


def parse_flex_report(xml_string: str) -> Dict[str, Any]:
    """
    Parse IBKR Flex statement XML into a flat dict.

    IBKR stores row data mostly as XML attributes (camelCase). Missing fields
    become None or 0.0; invalid XML or unexpected structure does not raise.
    """
    raw = xml_string if xml_string is not None else ""
    empty = _empty_parsed_result(raw)
    if not raw.strip():
        logger.error("parse_flex_report: empty xml_string")
        return empty

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.error("parse_flex_report: XML parse error: %s", exc)
        return empty

    try:
        stmt = _first_flex_statement(root)
        if stmt is None:
            logger.error("parse_flex_report: no FlexStatement element found")
            return empty

        ibkr_timestamp = _attr(stmt, "whenGenerated")
        account_id = _attr(stmt, "accountId")
        ai = stmt.find("AccountInformation")
        if not account_id:
            account_id = _attr(ai, "accountId")
        base_currency = _attr(ai, "currency") or "EUR"

        if not ibkr_timestamp:
            logger.warning("IBKR timestamp missing")

        positions: List[Dict[str, Any]] = []
        for op in stmt.iter("OpenPosition"):
            quantity = (
                _to_float(op.get("position"))
                if op.get("position") is not None
                else _to_float(op.get("quantity"))
            )
            positions.append(
                {
                    "symbol": op.get("symbol"),
                    "description": _attr(op, "description"),
                    "quantity": quantity,
                    "market_price": _to_float(op.get("markPrice")),
                    "market_value": _to_float(op.get("positionValue")),
                    "currency": _attr(op, "currency"),
                    "asset_class": _attr(op, "assetCategory"),
                }
            )

        if not positions:
            logger.warning("No positions found in IBKR report")

        cash_balances: List[Dict[str, Any]] = []
        for c in stmt.iter("CashReportCurrency"):
            bal = _to_float_optional(c.get("endingCash"))
            if bal is None:
                bal = _to_float_optional(c.get("endingSettledCash"))
            if bal is None:
                bal = _to_float_optional(c.get("totalCashValue"))
            if bal is None:
                bal = _to_float_optional(c.get("slbNetCash"))
            if bal is None:
                bal = 0.0
            cash_balances.append(
                {
                    "currency": _attr(c, "currency"),
                    "balance": bal,
                }
            )

        net_liquidation_value = _extract_net_liquidation(stmt)
        if net_liquidation_value == 0.0:
            logger.warning("Net liquidation not found, computing from positions + cash")
            positions_total = sum(p.get("market_value", 0.0) for p in positions)
            cash_total = sum(c.get("balance", 0.0) for c in cash_balances)
            net_liquidation_value = positions_total + cash_total

        return {
            "ibkr_timestamp": ibkr_timestamp,
            "account_id": account_id,
            "positions": positions,
            "cash_balances": cash_balances,
            "net_liquidation_value": float(net_liquidation_value),
            "base_currency": base_currency,
            "raw_xml": raw,
        }
    except Exception as exc:
        logger.error("parse_flex_report: unexpected error: %s", exc, exc_info=True)
        return empty


def request_flex_report() -> str:
    """Request Flex report generation and return IBKR reference code."""
    token = _env_required("IBKR_FLEX_TOKEN")
    query_id = _env_required("IBKR_FLEX_QUERY_ID")

    params = {"t": token, "q": query_id, "v": "3"}
    logger.info("Requesting IBKR Flex report (query_id=%s)", query_id)

    try:
        response = requests.get(
            _SEND_REQUEST_URL,
            params=params,
            headers=_HTTP_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("IBKR Flex SendRequest HTTP error: %s", exc)
        raise Exception(f"IBKR Flex SendRequest HTTP error: {exc}") from exc

    _validate_flex_http_body(response.text, "IBKR Flex SendRequest")
    response_xml = response.text
    try:
        root = ET.fromstring(response_xml)
    except ET.ParseError as exc:
        logger.error("IBKR Flex SendRequest invalid XML response")
        raise Exception("IBKR Flex SendRequest returned invalid XML response") from exc

    status = (_xml_text(root, "Status") or "").lower()
    if status != "success":
        error_code = _xml_text(root, "ErrorCode") or "unknown"
        error_message = _xml_text(root, "ErrorMessage") or "Unknown IBKR error"
        logger.error("IBKR Flex SendRequest failed: [%s] %s", error_code, error_message)
        raise Exception(f"IBKR Flex SendRequest failed: [{error_code}] {error_message}")

    reference_code = _xml_text(root, "ReferenceCode")
    if not reference_code:
        logger.error("IBKR Flex SendRequest succeeded but no ReferenceCode found")
        raise Exception("IBKR Flex SendRequest succeeded but ReferenceCode is missing")

    logger.info("IBKR Flex reference_code=%s", reference_code)
    logger.info("IBKR Flex report request accepted (reference_code=%s)", reference_code)
    return reference_code


def fetch_flex_report(reference_code: str) -> str:
    """Fetch Flex statement XML by reference code and return raw XML string."""
    token = _env_required("IBKR_FLEX_TOKEN")
    ref = (reference_code or "").strip()
    if not ref:
        raise Exception("reference_code is required")

    logger.info(
        "Fetching IBKR Flex report (reference_code=%s) with initial wait=%ss",
        ref,
        _INITIAL_WAIT_SECONDS,
    )
    time.sleep(_INITIAL_WAIT_SECONDS)

    params = {"t": token, "q": ref, "v": "3"}
    max_retries = len(_RETRY_DELAYS_SECONDS)
    max_attempts = 1 + max_retries  # first attempt + retries

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(
                _GET_STATEMENT_URL,
                params=params,
                headers=_HTTP_HEADERS,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("IBKR Flex GetStatement HTTP error on attempt %s/%s: %s", attempt, max_attempts, exc)
            raise Exception(f"IBKR Flex GetStatement HTTP error: {exc}") from exc

        _validate_flex_http_body(response.text, "IBKR Flex GetStatement")
        response_xml = response.text
        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError:
            logger.info("IBKR Flex report ready (non-wrapper XML received) on attempt %s/%s", attempt, max_attempts)
            return response_xml

        error_code = _xml_text(root, "ErrorCode")
        if error_code == "1019":
            if attempt <= max_retries:
                delay = _RETRY_DELAYS_SECONDS[attempt - 1]
                logger.info(
                    "IBKR Flex report not ready yet (error=1019), retrying in %ss (%s/%s)",
                    delay,
                    attempt,
                    max_retries,
                )
                time.sleep(delay)
                continue

            logger.error("IBKR Flex report still not ready after %s retries", max_retries)
            raise Exception(f"IBKR Flex report not ready after {max_retries} retries (error 1019)")

        status = (_xml_text(root, "Status") or "").lower()
        if status == "fail":
            error_message = _xml_text(root, "ErrorMessage") or "Unknown IBKR error"
            logger.error("IBKR Flex GetStatement failed: [%s] %s", error_code or "unknown", error_message)
            raise Exception(f"IBKR Flex GetStatement failed: [{error_code or 'unknown'}] {error_message}")

        logger.info("IBKR Flex report fetched successfully on attempt %s/%s", attempt, max_attempts)
        return response_xml

    raise Exception("IBKR Flex GetStatement failed unexpectedly")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ref_code = request_flex_report()
    print(fetch_flex_report(ref_code))
