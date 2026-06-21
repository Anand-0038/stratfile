"""Executor service: FastAPI on :8080. All endpoints from Build Spec §8.

Stateless across restarts except the DuckDB volume (strategies, runs, identity).
Every POST validates against stratfile.schema.json + the §4 invariants and rejects
with RFC 7807 problem details on failure.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import __version__, config
from ..canonical import content_hash
from ..data.cache import _conn, _db_lock
from ..generator import validator
from . import identity, trader

log = logging.getLogger("stratfile.executor")


def autoload_examples() -> None:
    """Load reference examples so the demo works immediately after startup."""
    try:
        example_dir = config.repo_root() / "examples"
    except config.ConfigError:
        return
    for path in sorted(example_dir.glob("*.stratfile.json")):
        try:
            doc = json.loads(path.read_text())
            receipt_path = path.with_name(path.name.replace(".stratfile.json", ".receipt.json"))
            receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
            _store_strategy(doc, receipt)
            log.info("Autoloaded example %s", path.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not autoload %s: %s", path.name, exc)
    try:
        identity.register("stratfile-executor", dry_run=not _live_chain_configured())
    except Exception as exc:  # noqa: BLE001
        log.warning("Identity registration skipped/soft-failed (dry-run mode active): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    autoload_examples()
    yield


app = FastAPI(
    title="Stratfile Executor",
    version=__version__,
    description="Loads stratfile.json artifacts, holds an ERC-8004 identity on BSC Testnet, "
    "and fires verifiable swaps on PancakeSwap V3 Testnet (gas-free via MegaFuel).",
    lifespan=lifespan,
)

_metrics_lock = threading.Lock()
_counters: dict[str, int] = {
    "stratfile_requests_total": 0,
    "stratfile_txs_total": 0,
    "stratfile_dry_runs_total": 0,
    "stratfile_evaluations_total": 0,
    "stratfile_strategies_loaded_total": 0,
}


def _bump(name: str, by: int = 1) -> None:
    with _metrics_lock:
        _counters[name] = _counters.get(name, 0) + by


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


@app.middleware("http")
async def count_requests(request: Request, call_next):
    _bump("stratfile_requests_total")
    return await call_next(request)


def _live_chain_configured() -> bool:
    return config.executor_private_key() is not None and bool(config.ERC8004_REGISTRY)


def _store_strategy(doc: dict, receipt: dict | None = None) -> dict:
    problems = validator.validate(doc)
    if problems:
        raise validator.ValidationError("; ".join(problems))
    chash = content_hash(doc)
    strategy_id = chash.split(":", 1)[1][:12]
    with _db_lock, _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO strategies VALUES (?, ?, ?, ?, ?, ?)",
            [
                strategy_id,
                doc["name"],
                chash,
                json.dumps(doc),
                json.dumps(receipt) if receipt else None,
                datetime.now(timezone.utc),
            ],
        )
    _bump("stratfile_strategies_loaded_total")
    return {"strategy_id": strategy_id, "content_hash": chash, "validation": "ok"}


def _load_strategy(strategy_id: str) -> tuple[dict, dict | None] | None:
    with _db_lock, _conn() as con:
        row = con.execute(
            "SELECT stratfile, receipt FROM strategies WHERE strategy_id = ?", [strategy_id]
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0]), (json.loads(row[1]) if row[1] else None)


# --- Endpoints (§8) ------------------------------------------------------


@app.get("/health")
def health() -> dict:
    ident = identity.get_identity()
    return {
        "status": "ok",
        "version": __version__,
        "network": "bsc-testnet",
        "identity_address": ident["address"] if ident else None,
    }


@app.get("/identity")
def get_identity():
    ident = identity.get_identity()
    if ident is None:
        return _problem(404, "No identity", "Agent identity not registered yet. POST /identity/register.")
    return ident


@app.post("/identity/register")
def register_identity(name: str = "stratfile-executor", dry_run: bool = True):
    return identity.register(name, dry_run=dry_run)


@app.post("/strategies")
async def upload_strategy(
    stratfile: UploadFile = File(...), receipt: UploadFile | None = File(None)
):
    try:
        doc = json.loads(await stratfile.read())
    except json.JSONDecodeError as exc:
        return _problem(400, "Invalid JSON", f"stratfile.json: {exc}")
    receipt_doc = None
    if receipt is not None:
        try:
            receipt_doc = json.loads(await receipt.read())
        except json.JSONDecodeError as exc:
            return _problem(400, "Invalid JSON", f"receipt.json: {exc}")
    try:
        return _store_strategy(doc, receipt_doc)
    except validator.ValidationError as exc:
        return _problem(400, "Stratfile validation failed", str(exc))


@app.get("/strategies")
def list_strategies() -> list[dict]:
    with _db_lock, _conn() as con:
        rows = con.execute(
            "SELECT strategy_id, name, content_hash, loaded_at FROM strategies ORDER BY loaded_at"
        ).fetchall()
    return [
        {
            "strategy_id": r[0],
            "name": r[1],
            "content_hash": r[2],
            "loaded_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]


@app.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: str):
    found = _load_strategy(strategy_id)
    if found is None:
        return _problem(404, "Unknown strategy", f"No strategy with id {strategy_id!r}")
    doc, receipt_doc = found
    return {
        "strategy_id": strategy_id,
        "stratfile": doc,
        "receipt": receipt_doc,
        "regimes": doc["regimes"],
        "rules": doc["rules"],
    }


@app.post("/strategies/{strategy_id}/evaluate")
def evaluate_strategy(strategy_id: str):
    found = _load_strategy(strategy_id)
    if found is None:
        return _problem(404, "Unknown strategy", f"No strategy with id {strategy_id!r}")
    _bump("stratfile_evaluations_total")
    return trader.evaluate(found[0])


@app.post("/strategies/{strategy_id}/execute")
def execute_strategy(strategy_id: str, body: dict | None = None):
    found = _load_strategy(strategy_id)
    if found is None:
        return _problem(404, "Unknown strategy", f"No strategy with id {strategy_id!r}")
    dry_run = bool((body or {}).get("dry_run", True))
    try:
        result = trader.execute(strategy_id, found[0], dry_run=dry_run)
    except trader.TradeError as exc:
        return _problem(409, "Trade rejected", str(exc))
    except ConnectionError as exc:
        return _problem(502, "Chain unreachable", f"{exc}. Retry with dry_run=true for offline demo.")
    _bump("stratfile_dry_runs_total" if dry_run else "stratfile_txs_total")
    return result


@app.get("/receipts/{strategy_id}")
def get_receipt(strategy_id: str):
    found = _load_strategy(strategy_id)
    if found is None:
        return _problem(404, "Unknown strategy", f"No strategy with id {strategy_id!r}")
    _, receipt_doc = found
    if receipt_doc is None:
        return _problem(404, "No receipt", f"Strategy {strategy_id!r} has no receipt attached")
    return receipt_doc


@app.get("/runs")
def list_runs(strategy_id: str | None = None) -> list[dict]:
    query = "SELECT ts, strategy_id, tx_hash, regime, action, dry_run, pnl_delta_usd FROM runs"
    args: list = []
    if strategy_id:
        query += " WHERE strategy_id = ?"
        args.append(strategy_id)
    query += " ORDER BY ts DESC LIMIT 50"
    with _db_lock, _conn() as con:
        rows = con.execute(query, args).fetchall()
    return [
        {
            "ts": r[0].isoformat() if r[0] else None,
            "strategy_id": r[1],
            "tx_hash": r[2],
            "regime": r[3],
            "action": r[4],
            "dry_run": bool(r[5]),
            "pnl_delta_usd": r[6],
        }
        for r in rows
    ]


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    lines = []
    with _metrics_lock:
        for name, value in sorted(_counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
