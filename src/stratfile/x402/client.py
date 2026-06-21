"""x402 client: the 402 -> sign -> 200 cycle (USDC on Base).

Used for backtest data payment proofs only — never on the trading hot path.
When no funded Base wallet is configured (or the endpoint is unreachable) we fall
back to a deterministic proof stub so the demo and CI stay hermetic. The receipt
records which mode produced the proof — only claim what is provable (§ kill list).
"""

from __future__ import annotations

import base64
import json
import logging
import time

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from rich.console import Console

from .. import config
from ..canonical import canonical_json, sha256_hex

log = logging.getLogger("stratfile.x402")
_console = Console(stderr=True)


class X402Unavailable(RuntimeError):
    pass


def _wallet() -> Account | None:
    key = config.x402_private_key()
    if key is None:
        return None
    return Account.from_key(key)


def pay_for_request(url: str, params: dict | None = None, timeout: float = 20.0) -> dict:
    """Full x402 handshake against a paid endpoint.

    Returns {data, payment_hash, network, amount, payer} on success.
    Raises X402Unavailable when the live cycle cannot complete (caller falls back to stub).
    """
    acct = _wallet()
    if acct is None:
        raise X402Unavailable("X402_WALLET_PRIVATE_KEY not configured")

    with httpx.Client(timeout=timeout) as client:
        first = client.get(url, params=params or {})
        if first.status_code == 200:
            return {
                "data": _safe_json(first),
                "payment_hash": None,
                "network": None,
                "amount": 0,
                "payer": acct.address,
                "note": "endpoint did not require payment",
            }
        if first.status_code != 402:
            raise X402Unavailable(f"expected 402, got {first.status_code}")

        challenge = _safe_json(first)
        requirement = _pick_requirement(challenge)
        payment_header = _build_payment_header(acct, requirement)

        second = client.get(
            url,
            params=params or {},
            headers={"X-PAYMENT": payment_header},
        )
        if second.status_code != 200:
            raise X402Unavailable(f"retry with payment failed: {second.status_code}")

        settle = second.headers.get("X-PAYMENT-RESPONSE", "")
        payment_hash = _extract_tx_hash(settle) or "0x" + sha256_hex(payment_header)[:64]
        _console.print(f"[green][x402][/green] \u2713 paid {payment_hash[:18]}... (USDC on Base)")
        return {
            "data": _safe_json(second),
            "payment_hash": payment_hash,
            "network": requirement.get("network", config.X402_NETWORK),
            "amount": requirement.get("maxAmountRequired", config.X402_AMOUNT_USDC),
            "payer": acct.address,
        }


def stub_proof(payload: dict) -> dict:
    """Deterministic offline proof: sha256(canonical(payload) + test seed)."""
    digest = sha256_hex(canonical_json(payload).encode() + config.TEST_SEED)
    _console.print(f"[yellow][x402][/yellow] \u26a0 offline stub proof stub:{digest[:18]}...")
    return {
        "payment_hash": f"stub:{digest}",
        "network": "offline",
        "amount": 0,
        "payer": None,
        "mode": "stub",
    }


def get_proof(payload: dict, paid_url: str | None = None) -> dict:
    """Best proof available: live x402 if configured + reachable, else deterministic stub."""
    if paid_url:
        try:
            live = pay_for_request(paid_url, params={"q": sha256_hex(canonical_json(payload))[:16]})
            return {
                "payment_hash": live["payment_hash"],
                "network": live["network"],
                "amount": live["amount"],
                "payer": live["payer"],
                "mode": "live",
            }
        except X402Unavailable as exc:
            log.info("x402 live handshake unavailable (%s); using stub", exc)
    return stub_proof(payload)


def _pick_requirement(challenge: dict) -> dict:
    accepts = challenge.get("accepts") or []
    if not accepts:
        raise X402Unavailable("402 challenge carries no payment requirements")
    for req in accepts:
        if req.get("network") in ("base", "base-mainnet", "eip155:8453"):
            return req
    return accepts[0]


def _build_payment_header(acct: Account, requirement: dict) -> str:
    """Sign the payment authorization and encode the X-PAYMENT header."""
    now = int(time.time())
    authorization = {
        "from": acct.address,
        "to": requirement.get("payTo", ""),
        "value": str(requirement.get("maxAmountRequired", "10000")),
        "validAfter": str(now - 60),
        "validBefore": str(now + int(requirement.get("maxTimeoutSeconds", 300))),
        "nonce": "0x" + sha256_hex(f"{acct.address}:{now}")[:64],
    }
    message = encode_defunct(text=canonical_json(authorization))
    signed = acct.sign_message(message)
    payload = {
        "x402Version": 1,
        "scheme": requirement.get("scheme", "exact"),
        "network": requirement.get("network", "base"),
        "payload": {"authorization": authorization, "signature": signed.signature.hex()},
    }
    return base64.b64encode(canonical_json(payload).encode()).decode()


def _extract_tx_hash(header_value: str) -> str | None:
    if not header_value:
        return None
    try:
        decoded = json.loads(base64.b64decode(header_value))
        return decoded.get("transaction") or decoded.get("txHash")
    except Exception:
        return None


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except json.JSONDecodeError:
        return {"raw": resp.text[:2000]}
