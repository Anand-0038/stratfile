"""AST -> stratfile.json. Rule-based, deterministic: same AST + seed = same bytes
(excluding metadata.created_at, per §13). The LLM never touches anything here."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import SCHEMA_URL, SPEC_VERSION
from .explainer import explain
from .parser import StrategyAST


def compose(
    ast: StrategyAST, seed: int = 42, author: str = "Anand-0037", use_llm: bool = False
) -> dict:
    primary = ast.primary
    rotate_to = ast.rotate_target
    universe = sorted({primary, rotate_to})

    name = f"{primary.lower()}-fear-greed-dca"

    data_sources = [
        {"id": "fng", "provider": "cmc", "tool": "fear_greed_index", "shift": -1},
        {
            "id": f"{primary.lower()}_price",
            "provider": "cmc",
            "tool": "price_quote",
            "args": {"symbol": primary},
            "shift": -1,
        },
    ]

    signals: list[dict] = []
    regimes: list[dict] = []
    rules: list[dict] = []
    conds: list[str] = []

    if ast.buy_threshold:
        signals.append(
            {
                "id": "extreme_fear",
                "type": "threshold",
                "source_ref": "fng",
                "params": {"op": ast.buy_threshold.op, "value": ast.buy_threshold.value},
            }
        )
        regimes.append({"id": "risk_on", "label": "Risk-On (DCA)", "condition": "extreme_fear"})
        rules.append(
            {
                "when_regime": "risk_on",
                "action": "buy",
                "target": primary,
                "size_pct": ast.size_pct,
                "cooldown_h": 24,
            }
        )
        conds.append("!extreme_fear")

    if ast.rotate_threshold:
        signals.append(
            {
                "id": "extreme_greed",
                "type": "threshold",
                "source_ref": "fng",
                "params": {"op": ast.rotate_threshold.op, "value": ast.rotate_threshold.value},
            }
        )
        regimes.append(
            {
                "id": "risk_off",
                "label": f"Risk-Off (Rotate to {rotate_to})",
                "condition": "extreme_greed",
            }
        )
        rules.append(
            {
                "when_regime": "risk_off",
                "action": "rotate_to",
                "target": rotate_to,
                "size_pct": 100,
                "cooldown_h": 24,
            }
        )
        conds.append("!extreme_greed")

    regimes.append(
        {"id": "neutral", "label": "Neutral (Hold)", "condition": " && ".join(conds)}
    )
    rules.append({"when_regime": "neutral", "action": "hold", "size_pct": 0, "cooldown_h": 0})

    stratfile = {
        "$schema": SCHEMA_URL,
        "version": SPEC_VERSION,
        "name": name,
        "description": explain(ast, seed=seed, use_llm=use_llm),
        "author": author,
        "universe": universe,
        "data_sources": data_sources,
        "signals": signals,
        "regimes": regimes,
        "rules": rules,
        "guardrails": {
            "max_drawdown_pct": 20,
            "per_trade_cap_pct": 10,
            "daily_turnover_cap_pct": 25,
            "slippage_cap_bps": 150,
            "cooldown_min": 30,
            "kill_switch_target": rotate_to,
        },
        "execution": {
            "network": "bsc-testnet",
            "dex": "pancakeswap-v3-testnet",
            "identity_contract": "ERC-8004",
            "paymaster": "megafuel",
            "gas_strategy": "paymaster-first",
        },
        "metadata": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tags": sorted({"dca", "fear-greed", primary.lower()}),
            "seed": seed,
            "prompt": ast.prompt,
        },
    }
    return stratfile
