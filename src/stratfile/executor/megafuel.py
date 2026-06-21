"""MegaFuel paymaster wrapper (NodeReal) — gas-free txs on BSC Testnet.

Sponsored flow: sign with gasPrice=0 and submit through the MegaFuel sponsor RPC.
If the paymaster is rate-limited or down (risk #8), fall back to normal gas via
the standard RPCs — the backup wallet must hold faucet BNB for that path.
"""

from __future__ import annotations

import logging

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .. import config

log = logging.getLogger("stratfile.megafuel")


def get_web3(sponsored: bool = False) -> Web3:
    urls = [config.MEGAFUEL_TESTNET_RPC] if sponsored else []
    urls += config.bsc_rpc_urls()
    last_exc: Exception | None = None
    for url in urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
            # BSC Testnet is PoA-like and returns long block extraData; inject middleware
            # so block decoding works (otherwise identity registration fails before sending tx).
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if w3.is_connected():
                log.info("Connected to RPC %s", url)
                return w3
        except Exception as exc:  # noqa: BLE001 — try next RPC
            last_exc = exc
    raise ConnectionError(f"No BSC Testnet RPC reachable (tried {len(urls)}): {last_exc}")


def send_transaction(w3: Web3, account, tx: dict) -> str:
    """Sign and send, paymaster-first. Returns tx hash hex."""
    sponsored = config.megafuel_policy_id() is not None
    # Include pending txs to avoid nonce collisions during multi-step flows
    # (approve + swap in quick succession).
    tx.setdefault("nonce", w3.eth.get_transaction_count(account.address, "pending"))
    tx.setdefault("chainId", config.BSC_TESTNET_CHAIN_ID)
    tx_to_sign = dict(tx)
    tx_to_sign.pop("from", None)

    if sponsored:
        try:
            # Keep EIP-1559 fields; force zero-fee intent for paymaster path.
            tx_sponsored = dict(tx_to_sign, maxFeePerGas=0, maxPriorityFeePerGas=0)
            signed = account.sign_transaction(tx_sponsored)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            return tx_hash.hex()
        except Exception as exc:  # noqa: BLE001
            log.warning("MegaFuel sponsored send failed (%s); falling back to normal gas", exc)

    # Normal fallback: EIP-1559 fees on BSC testnet.
    latest = w3.eth.get_block("latest")
    base_fee = int(latest.get("baseFeePerGas", 0) or 0)
    priority = int(getattr(w3.eth, "max_priority_fee", 0) or 0)
    if priority <= 0:
        priority = max(int(w3.eth.gas_price * 0.1), 1)
    max_fee = max(base_fee * 2 + priority, w3.eth.gas_price)
    tx_normal = dict(tx_to_sign, maxFeePerGas=max_fee, maxPriorityFeePerGas=priority)
    tx_normal.pop("gasPrice", None)
    signed = account.sign_transaction(tx_normal)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()
