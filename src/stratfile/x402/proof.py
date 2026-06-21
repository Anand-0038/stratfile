"""Receipt proof verification: anyone can verify a receipt without trusting us.

Three checks:
  1. content hash  — recompute sha256(canonical_json(stratfile body)), see
     canonical.HASH_EXCLUDED_FIELDS (metadata + description)
  2. signature     — recover the ECDSA signer from the receipt payload
  3. x402 proof    — stub proofs are deterministically recomputable; live proofs
                     carry a Base tx hash checkable on a block explorer
"""

from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct

from .. import config
from ..canonical import canonical_json, content_hash, sha256_hex


def receipt_signing_payload(receipt: dict) -> str:
    body = {k: v for k, v in receipt.items() if k not in ("signature", "signer")}
    return canonical_json(body)


def recover_signer(receipt: dict) -> str:
    message = encode_defunct(text=receipt_signing_payload(receipt))
    return Account.recover_message(message, signature=receipt["signature"])


def verify_receipt(receipt: dict, stratfile: dict | None = None) -> dict:
    """Returns {ok, checks: {...}} — every check independently reported."""
    checks: dict[str, dict] = {}

    if stratfile is not None:
        expected = content_hash(stratfile)
        actual = receipt.get("stratfile", {}).get("content_hash")
        checks["content_hash"] = {
            "ok": expected == actual,
            "expected": expected,
            "actual": actual,
        }

    try:
        recovered = recover_signer(receipt)
        claimed = receipt.get("signer", {}).get("address")
        checks["signature"] = {
            "ok": bool(claimed) and recovered.lower() == claimed.lower(),
            "recovered": recovered,
            "claimed": claimed,
        }
    except Exception as exc:
        checks["signature"] = {"ok": False, "error": str(exc)}

    x402 = receipt.get("x402", {})
    payment_hash = x402.get("payment_hash", "")
    if x402.get("mode") == "stub" and payment_hash.startswith("stub:"):
        payload = receipt.get("x402_payload", {})
        digest = sha256_hex(canonical_json(payload).encode() + config.TEST_SEED)
        checks["x402"] = {
            "ok": payment_hash == f"stub:{digest}",
            "mode": "stub",
            "note": "deterministic offline proof; recomputed locally",
        }
    elif x402.get("mode") == "live":
        checks["x402"] = {
            "ok": payment_hash.startswith("0x") and len(payment_hash) >= 18,
            "mode": "live",
            "note": f"verify on Base explorer: {payment_hash}",
        }
    else:
        checks["x402"] = {"ok": False, "error": "missing or unknown x402 proof mode"}

    return {"ok": all(c.get("ok") for c in checks.values()), "checks": checks}
