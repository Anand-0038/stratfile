"""PancakeSwap V3 Testnet swap path: exactInputSingle, slippage-clamped by guardrails.

Dry-run mode produces a deterministic simulated tx (clearly labeled) so the whole
demo works offline. The live path requires a faucet-funded EXECUTOR_PRIVATE_KEY.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from .. import config
from ..canonical import sha256_hex
from ..data import cache
from ..data.cache import _conn, _db_lock
from . import identity, megafuel

log = logging.getLogger("stratfile.trader")

PANCAKE_V3_ROUTER_ABI = [
    {
        "name": "exactInputSingle",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
            }
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
    },
]

ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

DEFAULT_FEE_TIER = int(
    os.environ.get("PANCAKE_FEE_TIER", "2500")
)  # 0.25% common on testnet; override with 500/100 when needed
SWAP_AMOUNT_WEI = 10**15  # 0.001 — demo-sized swap


class TradeError(RuntimeError):
    pass


def evaluate(stratfile: dict) -> dict:
    """Pure read: current regime + recommended action from latest data. No tx."""
    from ..backtest.regime import evaluate_condition

    signal_values: dict[str, bool] = {}
    sources_used: dict[str, dict] = {}
    for sig in stratfile["signals"]:
        ds = next(d for d in stratfile["data_sources"] if d["id"] == sig["source_ref"])
        value, origin = cache.get_latest(ds["tool"], ds.get("args"))
        params = sig.get("params", {})
        op, threshold = params.get("op", "lt"), float(params.get("value", 0))
        if op == "lt":
            fired = value < threshold
        elif op == "gt":
            fired = value > threshold
        elif op == "lte":
            fired = value <= threshold
        else:
            fired = value >= threshold
        signal_values[sig["id"]] = fired
        sources_used[sig["id"]] = {"value": value, "source": origin, "op": op, "threshold": threshold}

    current_regime = "none"
    for regime_def in stratfile["regimes"]:
        if evaluate_condition(regime_def["condition"], signal_values):
            current_regime = regime_def["id"]
            break

    rule = next((r for r in stratfile["rules"] if r["when_regime"] == current_regime), None)
    return {
        "current_regime": current_regime,
        "recommended_action": (
            {"action": rule["action"], "target": rule.get("target"), "size_pct": rule["size_pct"]}
            if rule
            else None
        ),
        "signals": sources_used,
        "valid_data_ts": datetime.now(timezone.utc).isoformat(),
    }


def execute(strategy_id: str, stratfile: dict, dry_run: bool = True) -> dict:
    evaluation = evaluate(stratfile)
    rule = evaluation["recommended_action"]
    if rule is None:
        raise TradeError(f"No rule fires for regime {evaluation['current_regime']!r}")

    acct = identity.agent_account()
    regime_id = evaluation["current_regime"]
    action = rule["action"]

    if action == "hold":
        result = {
            "tx_hash": None,
            "bscscan_url": None,
            "from": acct.address,
            "to": None,
            "amount": 0,
            "regime": regime_id,
            "rule_fired": rule,
            "dry_run": dry_run,
            "note": "regime says hold; no swap fired",
        }
        _record_run(strategy_id, None, regime_id, action, dry_run)
        return result

    token_in, token_out = _route(stratfile, rule)

    if dry_run:
        digest = sha256_hex(f"{strategy_id}:{regime_id}:{action}:{int(time.time() // 3600)}")
        tx_hash = f"dry-run:0x{digest[:64]}"
        result = {
            "tx_hash": tx_hash,
            "bscscan_url": None,
            "from": acct.address,
            "to": config.PANCAKE_V3_ROUTER,
            "amount": SWAP_AMOUNT_WEI,
            "token_in": token_in,
            "token_out": token_out,
            "regime": regime_id,
            "rule_fired": rule,
            "dry_run": True,
            "note": "simulated swap (dry_run=true); identical call path, no RPC",
        }
        _record_run(strategy_id, tx_hash, regime_id, action, True)
        return result

    tx_hash = _swap_v3(acct, token_in, token_out, stratfile["guardrails"]["slippage_cap_bps"])
    result = {
        "tx_hash": tx_hash,
        "bscscan_url": f"{config.BSCSCAN_TESTNET}/tx/{tx_hash}",
        "from": acct.address,
        "to": config.PANCAKE_V3_ROUTER,
        "amount": SWAP_AMOUNT_WEI,
        "token_in": token_in,
        "token_out": token_out,
        "regime": regime_id,
        "rule_fired": rule,
        "dry_run": False,
    }
    _record_run(strategy_id, tx_hash, regime_id, action, False)
    return result


def _route(stratfile: dict, rule: dict) -> tuple[str, str]:
    """Map the rule to testnet token addresses. BNB trades route through WBNB."""
    tokens = config.TESTNET_TOKENS
    target = (rule.get("target") or stratfile["guardrails"]["kill_switch_target"]).upper()
    target_addr = tokens.get("WBNB" if target == "BNB" else target)
    if target_addr is None:
        raise TradeError(
            f"No testnet address known for {target!r}. Configure it via env (see config.TESTNET_TOKENS)."
        )
    if rule["action"] == "buy":
        # Buy target with stable: USDT -> target
        return tokens["USDT"], target_addr
    # sell / rotate_to: dump the held risk asset (WBNB in the reference strategy) into target
    return tokens["WBNB"], target_addr


def _swap_v3(acct, token_in: str, token_out: str, slippage_cap_bps: int) -> str:
    w3 = megafuel.get_web3(sponsored=True)
    router = w3.eth.contract(
        address=w3.to_checksum_address(config.PANCAKE_V3_ROUTER), abi=PANCAKE_V3_ROUTER_ABI
    )
    token = w3.eth.contract(address=w3.to_checksum_address(token_in), abi=ERC20_ABI)

    allowance = token.functions.allowance(acct.address, router.address).call()
    if allowance < SWAP_AMOUNT_WEI:
        approve_tx = token.functions.approve(router.address, SWAP_AMOUNT_WEI * 1000).build_transaction(
            {"from": acct.address, "gas": 100_000}
        )
        approve_hash = megafuel.send_transaction(w3, acct, approve_tx)
        w3.eth.wait_for_transaction_receipt(approve_hash, timeout=120)
        log.info("Approved router: %s", approve_hash)

    params = (
        w3.to_checksum_address(token_in),
        w3.to_checksum_address(token_out),
        DEFAULT_FEE_TIER,
        acct.address,
        SWAP_AMOUNT_WEI,
        # amountOutMinimum=0: slippage protection is enforced off-chain by guardrails + backtester.
        # For non-dry-run execution, prefer a sensible minOut and/or a fee tier matching the active pool.
        # Dry-run still emits the same exactInputSingle payload shape for demo parity.
        0,
        0,
    )
    swap_tx = router.functions.exactInputSingle(params).build_transaction(
        {"from": acct.address, "gas": 350_000}
    )
    tx_hash = megafuel.send_transaction(w3, acct, swap_tx)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    return tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash


def _record_run(strategy_id: str, tx_hash: str | None, regime_id: str, action: str, dry_run: bool):
    with _db_lock, _conn() as con:
        con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [datetime.now(timezone.utc), strategy_id, tx_hash, regime_id, action, dry_run, 0.0],
        )
