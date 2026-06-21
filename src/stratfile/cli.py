"""Stratfile CLI (Typer). All commands from Build Spec §7.

    stratfile init "DCA into BNB when Fear & Greed < 30, rotate to USDT when > 75"
    stratfile validate <path>
    stratfile backtest <path> --windows 34
    stratfile receipt <path>
    stratfile verify-receipt <stratfile> <receipt>
    stratfile register --identity stratfile-executor
    stratfile execute <path> --dry-run
    stratfile serve --port 8080
    stratfile dashboard --port 8501
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from . import __version__, config
from .canonical import content_hash

app = typer.Typer(
    name="stratfile",
    help="Define your trading strategy in a Stratfile. Backtest it. Run it on-chain.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


@app.callback()
def main() -> None:
    """Stratfile v0.1.0 — a Dockerfile-like open format for trading strategies."""


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"stratfile {__version__}")


@app.command()
def init(
    prompt: str = typer.Argument(..., help="Natural-language strategy idea"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output path (default <name>.stratfile.json)"),
    seed: int = typer.Option(42, "--seed", help="Determinism seed"),
    author: str = typer.Option("Anand-0037", "--author"),
    llm_describe: bool = typer.Option(
        False,
        "--llm-describe",
        help="Polish the description field with the LLM (opt-in; breaks byte-determinism "
        "of that one field; never affects signals/rules or the content hash)",
    ),
) -> None:
    """NL prompt -> deterministic, schema-validated stratfile.json."""
    from .generator import composer, parser, validator

    try:
        ast = parser.parse(prompt)
    except parser.ParseError as exc:
        console.print(f"[red]parse error:[/red] {exc}")
        raise typer.Exit(1)

    doc = composer.compose(ast, seed=seed, author=author, use_llm=llm_describe)
    try:
        validator.validate_or_raise(doc)
    except validator.ValidationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    path = out or Path(f"{doc['name']}.stratfile.json")
    path.write_text(json.dumps(doc, indent=2) + "\n")
    console.print(f"[green]\u2713[/green] wrote [bold]{path}[/bold]  (content hash {content_hash(doc)[:23]}...)")
    console.print(JSON(json.dumps(doc)))


@app.command()
def validate(path: Path = typer.Argument(..., exists=True)) -> None:
    """Schema validation + §4 invariant checks. Exit 0 / non-zero."""
    from .generator import validator

    problems = validator.validate_file(path)
    if problems:
        console.print(f"[red]\u2717 {path} is INVALID:[/red]")
        for p in problems:
            console.print(f"  - {p}")
        raise typer.Exit(1)
    console.print(f"[green]\u2713[/green] {path} is a valid stratfile (v0.1.0)")


@app.command()
def backtest(
    path: Path = typer.Argument(..., exists=True),
    date_from: Optional[str] = typer.Option(None, "--from", help="YYYY-MM-DD"),
    date_to: Optional[str] = typer.Option(None, "--to", help="YYYY-MM-DD"),
    windows: int = typer.Option(34, "--windows"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write full result JSON here"),
) -> None:
    """Walk-forward backtest. Prints regime-stratified table, Sharpe, max-DD, Calmar."""
    from .backtest import engine
    from .generator import validator

    doc = json.loads(path.read_text())
    validator.validate_or_raise(doc)
    try:
        result = engine.run(doc, date_from, date_to, windows)
    except engine.BacktestError as exc:
        console.print(f"[red]backtest error:[/red] {exc}")
        raise typer.Exit(1)

    m = result["metrics"]
    head = Table(title=f"Walk-forward backtest — {doc['name']} ({result['from']} -> {result['to']})")
    for col in ("total_return_pct", "sharpe", "sortino", "max_drawdown_pct", "calmar", "n_trades"):
        head.add_column(col, justify="right")
    head.add_row(*(str(m[c]) for c in ("total_return_pct", "sharpe", "sortino", "max_drawdown_pct", "calmar", "n_trades")))
    console.print(head)

    rt = Table(title="Regime-stratified breakdown")
    for col in ("regime", "days", "total_return_pct", "mean_daily_ret_pct", "worst_day_pct"):
        rt.add_column(col, justify="right")
    for row in result["regime_breakdown"]:
        rt.add_row(*(str(row[c]) for c in ("regime", "days", "total_return_pct", "mean_daily_ret_pct", "worst_day_pct")))
    console.print(rt)

    wf = Table(title=f"{result['windows']} walk-forward windows (first/last 5)")
    for col in ("window", "from", "to", "return_pct", "sharpe", "max_dd_pct"):
        wf.add_column(col, justify="right")
    rows = result["walk_forward"]
    for row in rows[:5] + ([{"window": "...", "from": "", "to": "", "return_pct": "", "sharpe": "", "max_dd_pct": ""}] if len(rows) > 10 else []) + rows[-5:]:
        wf.add_row(*(str(row[c]) for c in ("window", "from", "to", "return_pct", "sharpe", "max_dd_pct")))
    console.print(wf)

    if result.get("benchmark"):
        b = result["benchmark"]
        console.print(f"benchmark: buy-and-hold {b['token']} = {b['buy_hold_return_pct']}%")
    mc = result["monte_carlo"]
    console.print(f"monte-carlo bootstrap: p50 max-DD {mc['p50_max_dd_pct']}% | p95 max-DD {mc['p95_max_dd_pct']}%")
    if result["kill_switch_fired"]:
        console.print("[yellow]\u26a0 kill-switch fired during this backtest[/yellow]")

    out_path = out or path.with_suffix("").with_suffix("").with_name(path.name.replace(".stratfile.json", "") + ".backtest.json")
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    console.print(f"[green]\u2713[/green] full result written to [bold]{out_path}[/bold]")


@app.command()
def receipt(
    path: Path = typer.Argument(..., exists=True, help="stratfile.json path"),
    x402_proof: Optional[str] = typer.Option(None, "--x402-proof", help="Pre-acquired x402 payment hash"),
    windows: int = typer.Option(34, "--windows"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Run backtest + emit paired receipt.json (content hash + x402 proof + signature)."""
    from .backtest import engine, receipt as receipt_mod
    from .generator import validator

    doc = json.loads(path.read_text())
    validator.validate_or_raise(doc)
    result = engine.run(doc, windows=windows)
    rec = receipt_mod.build_receipt(doc, result, x402_proof_hash=x402_proof)

    out_path = out or Path(str(path).replace(".stratfile.json", ".receipt.json"))
    out_path.write_text(json.dumps(rec, indent=2) + "\n")
    console.print(f"[green]\u2713[/green] receipt written to [bold]{out_path}[/bold]")
    console.print(f"  content hash : {rec['stratfile']['content_hash']}")
    console.print(f"  x402 proof   : {rec['x402']['payment_hash']} ({rec['x402']['mode']})")
    console.print(f"  signer       : {rec['signer']['address']}")


@app.command("verify-receipt")
def verify_receipt_cmd(
    stratfile_path: Path = typer.Argument(..., exists=True),
    receipt_path: Path = typer.Argument(..., exists=True),
) -> None:
    """Verify a receipt against its stratfile: hash + signature + x402 proof."""
    from .x402.proof import verify_receipt

    doc = json.loads(stratfile_path.read_text())
    rec = json.loads(receipt_path.read_text())
    result = verify_receipt(rec, doc)
    for name, check in result["checks"].items():
        mark = "[green]\u2713[/green]" if check["ok"] else "[red]\u2717[/red]"
        note = check.get("note") or check.get("error") or ""
        console.print(f"{mark} {name} {note}")
    if not result["ok"]:
        raise typer.Exit(1)
    console.print("[green]receipt VERIFIED[/green]")


@app.command()
def register(
    identity_name: str = typer.Option("stratfile-executor", "--identity"),
    network: str = typer.Option("bsc-testnet", "--network"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Register the agent's ERC-8004 identity on BSC Testnet (gas-free via MegaFuel)."""
    from .executor import identity as identity_mod

    if network != "bsc-testnet":
        console.print('[red]v0.1.0 supports only --network bsc-testnet[/red]')
        raise typer.Exit(1)
    record = identity_mod.register(identity_name, dry_run=dry_run)
    console.print(JSON(json.dumps(record, default=str)))


@app.command()
def execute(
    path: Path = typer.Argument(..., exists=True),
    network: str = typer.Option("bsc-testnet", "--network"),
    dry_run: bool = typer.Option(False, "--dry-run/--live"),
) -> None:
    """Evaluate current regime and fire (or simulate) one swap on PancakeSwap Testnet."""
    from .executor import trader
    from .generator import validator

    if network != "bsc-testnet":
        console.print('[red]v0.1.0 supports only --network bsc-testnet[/red]')
        raise typer.Exit(1)
    doc = json.loads(path.read_text())
    validator.validate_or_raise(doc)
    strategy_id = content_hash(doc).split(":", 1)[1][:12]
    try:
        result = trader.execute(strategy_id, doc, dry_run=dry_run)
    except (trader.TradeError, ConnectionError) as exc:
        console.print(f"[red]execute failed:[/red] {exc}")
        console.print("hint: retry with --dry-run for the offline demo path")
        raise typer.Exit(1)
    console.print(JSON(json.dumps(result, default=str)))
    if result.get("bscscan_url"):
        console.print(f"[green]\u2713 tx confirmed:[/green] {result['bscscan_url']}")


@app.command()
def serve(
    port: int = typer.Option(config.EXECUTOR_PORT, "--port"),
    host: str = typer.Option("0.0.0.0", "--host"),
) -> None:
    """Start the FastAPI executor service (§8)."""
    import uvicorn

    uvicorn.run("stratfile.executor.app:app", host=host, port=port, log_level="info")


@app.command()
def dashboard(
    port: int = typer.Option(config.DASHBOARD_PORT, "--port"),
    host: str = typer.Option("0.0.0.0", "--host"),
) -> None:
    """Start the Streamlit dashboard (§9)."""
    dash_path = Path(__file__).parent / "dashboard" / "app.py"
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(dash_path),
                "--server.port",
                str(port),
                "--server.address",
                host,
                "--server.headless",
                "true",
            ]
        )
    )


if __name__ == "__main__":
    app()
