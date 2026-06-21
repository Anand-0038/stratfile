"""receipt.json builder: content hash + x402 proof + ECDSA signature.

This is the wedge artifact. Every stratfile carries a receipt; every receipt is
verifiable by anyone with `stratfile verify-receipt` (or x402.proof.verify_receipt)
— no trusted-server step (anti-pattern from the Numerai analysis).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from eth_account import Account
from eth_account.messages import encode_defunct

from .. import SPEC_VERSION, config
from ..canonical import content_hash, sha256_hex
from ..x402 import client as x402_client
from ..x402.proof import receipt_signing_payload

log = logging.getLogger("stratfile.receipt")

# CMC x402-paid data endpoint (USDC on Base). Used only when a funded wallet exists.
X402_PAID_ENDPOINT = "https://api.coinmarketcap.com/x402/fear-and-greed/latest"


def _signing_account() -> Account:
    key = config.x402_private_key() or config.executor_private_key()
    if key is None:
        # Deterministic testnet-only fallback key — receipts remain verifiable,
        # the receipt just declares the signer is the shared demo identity.
        key = "0x" + sha256_hex(config.TEST_SEED)
    return Account.from_key(key)


def build_receipt(
    stratfile: dict,
    backtest_result: dict,
    x402_proof_hash: str | None = None,
) -> dict:
    """Build + sign a receipt for a (stratfile, backtest) pair."""
    chash = content_hash(stratfile)

    x402_payload = {
        "content_hash": chash,
        "backtest_from": backtest_result["from"],
        "backtest_to": backtest_result["to"],
        "windows": backtest_result["windows"],
    }

    if x402_proof_hash:
        x402_block = {
            "payment_hash": x402_proof_hash,
            "network": config.X402_NETWORK,
            "amount": config.X402_AMOUNT_USDC,
            "payer": None,
            "mode": "live" if x402_proof_hash.startswith("0x") else "stub",
        }
    else:
        x402_block = x402_client.get_proof(x402_payload, paid_url=X402_PAID_ENDPOINT)

    acct = _signing_account()
    receipt = {
        "receipt_version": SPEC_VERSION,
        "stratfile": {
            "name": stratfile["name"],
            "content_hash": chash,
            "schema": stratfile["$schema"],
        },
        "backtest": {
            "from": backtest_result["from"],
            "to": backtest_result["to"],
            "windows": backtest_result["windows"],
            "metrics": backtest_result["metrics"],
            "regime_breakdown": backtest_result["regime_breakdown"],
            "monte_carlo": backtest_result["monte_carlo"],
            "benchmark": backtest_result.get("benchmark"),
            "kill_switch_fired": backtest_result.get("kill_switch_fired", False),
        },
        "x402": x402_block,
        "x402_payload": x402_payload,
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "SLSA-L1-style build provenance for trading strategies",
    }

    message = encode_defunct(text=receipt_signing_payload(receipt))
    signed = acct.sign_message(message)
    sig_hex = signed.signature.hex()
    receipt["signature"] = sig_hex if sig_hex.startswith("0x") else "0x" + sig_hex
    receipt["signer"] = {"address": acct.address, "algorithm": "eth_personal_sign(secp256k1)"}
    return receipt
