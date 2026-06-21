"""Central configuration. All env vars documented in .env.example."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Fail closed with a clear message — never silently mutate the contract."""


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Locate the directory holding stratfile.schema.json (repo root or /app in Docker).

    Resolution order: $STRATFILE_HOME, walk up from CWD, walk up from this file.
    """
    candidates: list[Path] = []
    if env_home := os.environ.get("STRATFILE_HOME"):
        candidates.append(Path(env_home))
    cwd = Path.cwd()
    candidates.extend([cwd, *cwd.parents])
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    for cand in candidates:
        if (cand / "stratfile.schema.json").is_file():
            return cand
    raise ConfigError(
        "Cannot locate stratfile.schema.json. Run from the repo root or set STRATFILE_HOME."
    )


def schema_path() -> Path:
    return repo_root() / "stratfile.schema.json"


def fixtures_dir() -> Path:
    return repo_root() / "fixtures"


def duckdb_path() -> Path:
    p = os.environ.get("DUCKDB_PATH")
    if p:
        path = Path(p)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except PermissionError:
            # DUCKDB_PATH from .env typically targets the Docker volume (/data);
            # on the host, fall back to the repo-local data dir.
            pass
    path = repo_root() / "data-volume" / "stratfile.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# --- CMC ---------------------------------------------------------------
CMC_MCP_URL = os.environ.get("CMC_MCP_URL", "https://mcp.coinmarketcap.com/mcp")
CMC_REST_URL = os.environ.get("CMC_REST_URL", "https://pro-api.coinmarketcap.com")


def cmc_mcp_key() -> str | None:
    # mcp.coinmarketcap.com accepts the standard pro-API key in X-CMC-MCP-API-KEY,
    # so fall back to CMC_REST_KEY when no dedicated MCP key is configured.
    return _real(os.environ.get("CMC_MCP_KEY")) or _real(os.environ.get("CMC_REST_KEY"))


def cmc_rest_key() -> str | None:
    return _real(os.environ.get("CMC_REST_KEY"))


# --- x402 --------------------------------------------------------------
X402_FACILITATOR_URL = os.environ.get("X402_FACILITATOR_URL", "https://x402.org/facilitator")
X402_AMOUNT_USDC = 0.01
X402_NETWORK = "base"


def x402_private_key() -> str | None:
    return _real(os.environ.get("X402_WALLET_PRIVATE_KEY"))


# --- BSC Testnet -------------------------------------------------------
BSC_TESTNET_CHAIN_ID = 97
BSCSCAN_TESTNET = "https://testnet.bscscan.com"


def bsc_rpc_urls() -> list[str]:
    urls = [
        os.environ.get("BSC_TESTNET_RPC", "https://data-seed-prebsc-1-s1.binance.org:8545"),
        os.environ.get("BSC_TESTNET_RPC_BACKUP_1", "https://data-seed-prebsc-2-s1.binance.org:8545"),
        os.environ.get("BSC_TESTNET_RPC_BACKUP_2", "https://bsc-testnet.publicnode.com"),
    ]
    return [u for u in urls if u]


def executor_private_key() -> str | None:
    return _real(os.environ.get("EXECUTOR_PRIVATE_KEY"))


def megafuel_policy_id() -> str | None:
    return _real(os.environ.get("MEGAFUEL_POLICY_ID"))


# MegaFuel sponsor RPC (NodeReal) — sponsored txs are submitted through this endpoint.
MEGAFUEL_TESTNET_RPC = os.environ.get(
    "MEGAFUEL_TESTNET_RPC", "https://bsc-megafuel-testnet.nodereal.io"
)

# PancakeSwap V3 on BSC Testnet. Overridable via env; resolved at startup, never
# hard-coded into stratfile.json artifacts.
PANCAKE_V3_ROUTER = os.environ.get(
    "PANCAKE_V3_ROUTER", "0x1b81D678ffb9C0263b24A97847620C99d213eB14"
)
PANCAKE_V2_ROUTER = os.environ.get(
    "PANCAKE_V2_ROUTER", "0xD99D1c33F9fC3444f8101754aBC46c52416550D1"
)
# ERC-8004 Identity Registry on BSC Testnet — canonical deployment from the official
# bnb-chain/erc-8004-contracts repo (UUPS proxy, verified on BscScan Testnet).
# https://testnet.bscscan.com/address/0x8004A818BFB912233c491871b3d84c89A494BD9e
ERC8004_REGISTRY = os.environ.get(
    "ERC8004_REGISTRY_ADDRESS", "0x8004A818BFB912233c491871b3d84c89A494BD9e"
)

TESTNET_TOKENS = {
    "WBNB": os.environ.get("WBNB_TESTNET", "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"),
    "USDT": os.environ.get("USDT_TESTNET", "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd"),
    "BUSD": os.environ.get("BUSD_TESTNET", "0xeD24FC36d5Ee211Ea25A80239Fb8C4Cfd80f12Ee"),
}

# --- LLM (explainer only; never in the decision loop) -------------------
LLM_SEED = int(os.environ.get("LLM_SEED", "42"))


def openai_api_key() -> str | None:
    return _real(os.environ.get("OPENAI_API_KEY"))


# --- Service ------------------------------------------------------------
EXECUTOR_PORT = int(os.environ.get("EXECUTOR_PORT", "8080"))
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8501"))
EXECUTOR_URL = os.environ.get("EXECUTOR_URL", f"http://localhost:{os.environ.get('EXECUTOR_PORT', '8080')}")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Deterministic key used ONLY when no real key is configured (offline/dry-run demos).
TEST_SEED = b"stratfile-test-seed-42"


def _real(value: str | None) -> str | None:
    """Treat unset values and .env.example placeholders (<...>) as missing."""
    if not value or value.startswith("<"):
        return None
    return value
