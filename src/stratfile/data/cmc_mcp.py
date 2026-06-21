"""CMC MCP client (streamable HTTP transport) with a circuit breaker.

mcp.coinmarketcap.com is brand new and will flake (risk #2 in the dossier) — after
FAILURE_THRESHOLD consecutive failures the breaker opens for COOLDOWN_S seconds and
every call short-circuits to the REST fallback without touching the network.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from typing import Any

import httpx

from .. import config

log = logging.getLogger("stratfile.cmc_mcp")

FAILURE_THRESHOLD = 3
COOLDOWN_S = 120
_rpc_id = itertools.count(1)


class McpUnavailable(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, threshold: int = FAILURE_THRESHOLD, cooldown_s: float = COOLDOWN_S):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            # half-open: allow one probe
            self.opened_at = None
            self.failures = self.threshold - 1
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()
            log.warning("CMC MCP circuit breaker OPEN for %ss", self.cooldown_s)


_breaker = CircuitBreaker()


def _rpc(method: str, params: dict | None = None, timeout: float = 15.0) -> Any:
    key = config.cmc_mcp_key()
    if key is None:
        raise McpUnavailable("CMC_MCP_KEY not configured")
    if _breaker.open:
        raise McpUnavailable("circuit breaker open")

    payload = {"jsonrpc": "2.0", "id": next(_rpc_id), "method": method, "params": params or {}}
    try:
        resp = httpx.post(
            config.CMC_MCP_URL,
            json=payload,
            headers={
                "X-CMC-MCP-API-KEY": key,
                "Accept": "application/json, text/event-stream",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        body = _parse_response(resp)
        if "error" in body:
            raise McpUnavailable(f"MCP error: {body['error']}")
        _breaker.record_success()
        return body.get("result")
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        _breaker.record_failure()
        raise McpUnavailable(f"MCP call failed: {exc}") from exc


def _parse_response(resp: httpx.Response) -> dict:
    """Handle both plain JSON and SSE-framed (streamable HTTP) responses."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise json.JSONDecodeError("no data frame in SSE response", resp.text[:100], 0)
    return resp.json()


def list_tools() -> list[dict]:
    result = _rpc("tools/list")
    return result.get("tools", [])


def call_tool(name: str, arguments: dict | None = None) -> Any:
    result = _rpc("tools/call", {"name": name, "arguments": arguments or {}})
    # MCP tool results carry a content list; unwrap the first JSON/text item.
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                return item["text"]
    return result
