"""DuckDB cache + token-bucket limiter + the fallback chain.

Read path for any data tool:  DuckDB cache -> CMC MCP -> CMC REST -> checked-in fixtures.
Historical series always come from fixtures/cache (hermetic, reproducible — §13);
live calls are only used for the *latest* point in /evaluate, never in CI.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import duckdb
import pandas as pd

from .. import config
from . import cmc_mcp, cmc_rest

log = logging.getLogger("stratfile.cache")

# CMC free tier ~30 req/min — keep a margin.
RATE_PER_MIN = 25


class TokenBucket:
    def __init__(self, rate_per_min: float = RATE_PER_MIN, capacity: int | None = None):
        self.rate = rate_per_min / 60.0
        self.capacity = capacity or rate_per_min
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)


_bucket = TokenBucket()
_db_lock = threading.Lock()


def _conn() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(config.duckdb_path()))
    con.execute(
        """CREATE TABLE IF NOT EXISTS kv_cache (
               key TEXT PRIMARY KEY, fetched_at TIMESTAMP, payload TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS runs (
               ts TIMESTAMP, strategy_id TEXT, tx_hash TEXT, regime TEXT,
               action TEXT, dry_run BOOLEAN, pnl_delta_usd DOUBLE)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS strategies (
               strategy_id TEXT PRIMARY KEY, name TEXT, content_hash TEXT,
               stratfile TEXT, receipt TEXT, loaded_at TIMESTAMP)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS identity (
               address TEXT PRIMARY KEY, token_id BIGINT, name TEXT,
               metadata_uri TEXT, tx_hash TEXT, registered_at TIMESTAMP, dry_run BOOLEAN)"""
    )
    return con


def cache_get(key: str, max_age_s: float | None = None) -> dict | None:
    with _db_lock, _conn() as con:
        row = con.execute("SELECT fetched_at, payload FROM kv_cache WHERE key = ?", [key]).fetchone()
    if row is None:
        return None
    fetched_at, payload = row
    if max_age_s is not None:
        age = (datetime.now(timezone.utc) - fetched_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age > max_age_s:
            return None
    return json.loads(payload)


def cache_put(key: str, value: dict) -> None:
    with _db_lock, _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO kv_cache VALUES (?, ?, ?)",
            [key, datetime.now(timezone.utc), json.dumps(value)],
        )


# --- Historical series (fixtures-first, hermetic) -----------------------

_FIXTURE_FILES = {
    ("fear_greed_index", ""): ("fng_daily.csv", "value"),
    ("price_quote", "BNB"): ("bnb_usd_daily.csv", "close"),
    ("price_quote", "ETH"): ("eth_usd_daily.csv", "close"),
}


class DataUnavailable(RuntimeError):
    pass


def get_daily_series(tool: str, args: dict | None = None) -> pd.Series:
    """Daily historical series for a stratfile data_source. Fixture-backed."""
    args = args or {}
    symbol = str(args.get("symbol", ""))
    fixture = _FIXTURE_FILES.get((tool, symbol)) or _FIXTURE_FILES.get((tool, ""))
    if fixture is None:
        raise DataUnavailable(
            f"No fixture for tool={tool!r} args={args!r}. "
            f"Available: {sorted(_FIXTURE_FILES)}. Add a fixture CSV to fixtures/."
        )
    fname, col = fixture
    path = config.fixtures_dir() / fname
    if not path.is_file():
        raise DataUnavailable(f"Fixture file missing: {path}")
    df = pd.read_csv(path, parse_dates=["date"])
    series = df.set_index("date")[col].astype(float).sort_index()
    series.name = f"{tool}:{symbol}" if symbol else tool
    return series


# --- Latest point (live chain: MCP -> REST -> fixture tail) -------------

_TOOL_REST = {
    "fear_greed_index": lambda args: float(cmc_rest.fear_greed_latest()),
    "price_quote": lambda args: cmc_rest.price_quote(str(args.get("symbol", "BNB"))),
}

# CMC numeric IDs for the MCP quote tools (verified against the live tool schemas:
# get_crypto_quotes_latest takes {"id": "<cmc_id>"}).
_CMC_IDS = {
    "BTC": "1", "ETH": "1027", "USDT": "825", "BNB": "1839", "USDC": "3408",
    "XRP": "52", "ADA": "2010", "DOGE": "74", "SOL": "5426", "CAKE": "7186",
    "AVAX": "5805", "ATOM": "3794", "FIL": "2280", "SHIB": "5994", "BUSD": "4687",
}


def get_latest(tool: str, args: dict | None = None, max_age_s: float = 300) -> tuple[float, str]:
    """Latest value for a tool. Returns (value, source) where source is mcp|rest|cache|fixture."""
    args = args or {}
    key = f"latest:{tool}:{json.dumps(args, sort_keys=True)}"

    cached = cache_get(key, max_age_s=max_age_s)
    if cached is not None:
        return float(cached["value"]), "cache"

    if _bucket.acquire(timeout=2.0):
        try:
            mcp_value = _latest_via_mcp(tool, args)
            cache_put(key, {"value": mcp_value})
            return mcp_value, "mcp"
        except cmc_mcp.McpUnavailable as exc:
            log.info("MCP unavailable (%s); trying REST", exc)
        try:
            rest_fn = _TOOL_REST.get(tool)
            if rest_fn is not None:
                value = float(rest_fn(args))
                cache_put(key, {"value": value})
                return value, "rest"
        except cmc_rest.RestUnavailable as exc:
            log.info("REST unavailable (%s); falling back to fixture", exc)

    series = get_daily_series(tool, args)
    return float(series.iloc[-1]), "fixture"


def _latest_via_mcp(tool: str, args: dict) -> float:
    """Map stratfile data tools onto the 12 live CMC MCP tools (verified 2026-06-10)."""
    if tool == "fear_greed_index":
        # FNG ships inside get_global_metrics_latest -> sentiment.fear_greed.current.index
        payload = cmc_mcp.call_tool("get_global_metrics_latest", {})
        return float(payload["sentiment"]["fear_greed"]["current"]["index"])
    if tool == "price_quote":
        symbol = str(args.get("symbol", "BNB")).upper()
        cmc_id = _CMC_IDS.get(symbol)
        if cmc_id is None:
            raise cmc_mcp.McpUnavailable(f"no CMC id mapping for symbol {symbol}")
        payload = cmc_mcp.call_tool("get_crypto_quotes_latest", {"id": cmc_id})
        entry = payload[0] if isinstance(payload, list) else payload
        return float(entry["price"])
    raise cmc_mcp.McpUnavailable(f"no MCP mapping for tool {tool}")


def _extract_number(obj: object) -> float:
    """Best-effort numeric extraction from an MCP tool payload."""
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, str):
        return float(obj)
    if isinstance(obj, dict):
        for k in ("value", "price", "score"):
            if k in obj:
                return _extract_number(obj[k])
        for v in obj.values():
            try:
                return _extract_number(v)
            except (TypeError, ValueError):
                continue
    raise ValueError(f"cannot extract numeric value from MCP payload: {obj!r}")
