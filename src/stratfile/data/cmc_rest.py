"""CMC REST fallback (pro-api.coinmarketcap.com). Used when MCP throttles or flakes."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .. import config

log = logging.getLogger("stratfile.cmc_rest")


class RestUnavailable(RuntimeError):
    pass


def _get(path: str, params: dict | None = None, timeout: float = 15.0) -> Any:
    key = config.cmc_rest_key()
    if key is None:
        raise RestUnavailable("CMC_REST_KEY not configured")
    try:
        resp = httpx.get(
            f"{config.CMC_REST_URL}{path}",
            params=params or {},
            headers={"X-CMC_PRO_API_KEY": key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["data"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise RestUnavailable(f"REST call failed: {exc}") from exc


def fear_greed_latest() -> int:
    data = _get("/v3/fear-and-greed/latest")
    return int(data["value"])


def price_quote(symbol: str) -> float:
    data = _get("/v2/cryptocurrency/quotes/latest", {"symbol": symbol})
    entry = data[symbol]
    if isinstance(entry, list):
        entry = entry[0]
    return float(entry["quote"]["USD"]["price"])


def crypto_map(limit: int = 500) -> list[dict]:
    """Token universe with listing status preserved (survivorship-bias guard)."""
    return _get(
        "/v1/cryptocurrency/map",
        {"listing_status": "active,inactive,untracked", "limit": limit},
    )
