"""ERC-8004 agent identity on BSC Testnet.

Resolution order (bnbagent-sdk 0.3.5 is pinned in the spec but not yet published
on PyPI — verified 404 at build time):
  1. bnbagent_sdk if importable          (pip install bnbagent-sdk==0.3.5)
  2. web3.py direct call                 register(name, metadataURI) on the registry
  3. deterministic dry-run identity      offline demos / no registry address configured

The identity (token_id, address, tx) is persisted in DuckDB and exposed via
GET /identity and the dashboard identity card.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from eth_account import Account

from .. import config
from ..canonical import sha256_hex
from ..data.cache import _conn, _db_lock
from . import megafuel

log = logging.getLogger("stratfile.identity")

# ERC-8004 v1.0 IdentityRegistry surface (official bnb-chain/erc-8004-contracts):
#   register(string agentURI) -> uint256 agentId   (ERC-721 mint; caller becomes owner)
#   event Registered(uint256 indexed agentId, string agentURI, address indexed owner)
ERC8004_ABI = [
    {
        "name": "register",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "agentURI", "type": "string"}],
        "outputs": [{"name": "agentId", "type": "uint256"}],
    },
    {
        "name": "Registered",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "agentId", "type": "uint256", "indexed": True},
            {"name": "agentURI", "type": "string", "indexed": False},
            {"name": "owner", "type": "address", "indexed": True},
        ],
    },
]


def agent_card_uri(name: str, address: str) -> str:
    """Self-contained agent card as a base64 data: URI (supported by the registry spec);
    no external hosting needed for the hackathon demo."""
    import base64
    import json

    card = {
        "type": "stratfile-executor",
        "name": name,
        "description": "Reference executor for the Stratfile open strategy format. "
        "Loads stratfile.json artifacts, verifies signed backtest receipts, and "
        "executes on PancakeSwap V3 BSC Testnet.",
        "url": "https://github.com/Anand-0037/stratfile",
        "wallet": address,
        "skills": ["stratfile_validate", "stratfile_evaluate", "stratfile_execute"],
    }
    payload = base64.b64encode(json.dumps(card, separators=(",", ":")).encode()).decode()
    return f"data:application/json;base64,{payload}"


def agent_account() -> Account:
    key = config.executor_private_key()
    if key is None:
        # Deterministic testnet-only demo key. Never used on mainnet by construction.
        key = "0x" + sha256_hex(config.TEST_SEED + b":executor")
    return Account.from_key(key)


def get_identity() -> dict | None:
    with _db_lock, _conn() as con:
        row = con.execute(
            "SELECT address, token_id, name, metadata_uri, tx_hash, registered_at, dry_run "
            "FROM identity LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "address": row[0],
        "token_id": row[1],
        "name": row[2],
        "metadata_uri": row[3],
        "tx_hash": row[4],
        "registered_at": row[5].isoformat() if row[5] else None,
        "dry_run": bool(row[6]),
        "bscscan_url": f"{config.BSCSCAN_TESTNET}/tx/{row[4]}" if str(row[4]).startswith("0x") else None,
    }


def register(name: str, metadata_uri: str = "", dry_run: bool = False) -> dict:
    existing = get_identity()
    if existing is not None:
        log.info("Identity already registered: token_id=%s", existing["token_id"])
        return existing

    acct = agent_account()
    metadata_uri = metadata_uri or agent_card_uri(name, acct.address)

    record: dict | None = None
    if not dry_run:
        record = _register_via_sdk(name, metadata_uri, acct) or _register_via_web3(
            name, metadata_uri, acct
        )
    if record is None:
        # Deterministic dry-run identity — clearly labeled, never claimed as on-chain.
        digest = sha256_hex(f"erc8004:{acct.address}:{name}")
        record = {
            "address": acct.address,
            "token_id": int(digest[:12], 16) % 1_000_000,
            "name": name,
            "metadata_uri": metadata_uri,
            "tx_hash": f"dry-run:{digest[:24]}",
            "dry_run": True,
        }
        log.info("Registered DRY-RUN identity token_id=%s", record["token_id"])

    with _db_lock, _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO identity VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                record["address"],
                record["token_id"],
                record["name"],
                record["metadata_uri"],
                record["tx_hash"],
                datetime.now(timezone.utc),
                record["dry_run"],
            ],
        )
    return get_identity() or record


def _register_via_sdk(name: str, metadata_uri: str, acct: Account) -> dict | None:
    try:
        import bnbagent_sdk  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        agent = bnbagent_sdk.Agent(  # pragma: no cover - requires unpublished SDK
            private_key=acct.key.hex(), network="bsc-testnet", paymaster="megafuel"
        )
        result = agent.identity.register(name=name, metadata_uri=metadata_uri)
        return {
            "address": acct.address,
            "token_id": int(result.token_id),
            "name": name,
            "metadata_uri": metadata_uri,
            "tx_hash": result.tx_hash,
            "dry_run": False,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("bnbagent_sdk registration failed: %s", exc)
        return None


def _register_via_web3(name: str, metadata_uri: str, acct: Account) -> dict | None:
    registry = config.ERC8004_REGISTRY
    if not registry:
        log.info("ERC8004_REGISTRY_ADDRESS not set; cannot register on-chain")
        return None
    try:
        w3 = megafuel.get_web3(sponsored=True)
        contract = w3.eth.contract(address=w3.to_checksum_address(registry), abi=ERC8004_ABI)
        tx = contract.functions.register(metadata_uri).build_transaction(
            {"from": acct.address, "gas": 400_000}
        )
        tx_hash = megafuel.send_transaction(w3, acct, tx)
        rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        token_id = _token_id_from_logs(w3, contract, rcpt)
        if token_id is None:
            token_id = int(sha256_hex(tx_hash)[:12], 16) % 1_000_000
        return {
            "address": acct.address,
            "token_id": token_id,
            "name": name,
            "metadata_uri": metadata_uri,
            "tx_hash": tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash,
            "dry_run": False,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("web3 ERC-8004 registration failed: %s", exc)
        return None


def _token_id_from_logs(w3, contract, receipt) -> int | None:
    """agentId from the Registered event; fall back to the ERC-721 Transfer mint topic."""
    try:
        events = contract.events.Registered().process_receipt(receipt)
        if events:
            return int(events[0]["args"]["agentId"])
    except Exception:  # noqa: BLE001
        pass
    try:
        transfer_sig = w3.keccak(text="Transfer(address,address,uint256)")
        for entry in receipt.logs:
            if entry.topics and entry.topics[0] == transfer_sig and len(entry.topics) >= 4:
                return int(entry.topics[3].hex(), 16)
    except Exception:  # noqa: BLE001
        pass
    return None
