# Architecture

One JSON artifact (`stratfile.json` + paired `receipt.json`), two consumers: a human author
and an on-chain agent. The spec is the contract; everything else is replaceable.

```mermaid
graph TB
	U["Quant author<br>Claude Code / Cursor / stratfile CLI"]
	subgraph Generator
		G1["stratfile init<br>NL prompt parser"]
		G2["Rule-based composer<br>deterministic"]
		G3["LLM explainer<br>temp=0 seeded (description only)"]
		G4["Schema validator<br>JSON-Schema v0.1.0 + invariants"]
	end
	subgraph Data
		D1["CMC MCP<br>mcp.coinmarketcap.com<br>circuit breaker"]
		D2["CMC REST fallback<br>pro-api.coinmarketcap.com"]
		D3["DuckDB cache<br>token-bucket limiter<br>checked-in fixtures"]
	end
	subgraph Backtester
		B1["Walk-forward engine<br>34 disjoint windows"]
		B2["Regime stratifier"]
		B3["Receipt signer<br>SHA-256 + x402 proof + ECDSA"]
	end
	subgraph Artifact
		S1["stratfile.json"]
		S2["receipt.json"]
	end
	subgraph Executor
		E1["FastAPI :8080"]
		E2["ERC-8004 registry<br>BSC Testnet"]
		E3["MegaFuel paymaster<br>gas-free"]
		E4["PancakeSwap V3 Testnet<br>exactInputSingle"]
	end
	subgraph UI
		V1["Streamlit :8501"]
		V2["BscScan Testnet"]
	end
	U --> G1
	G1 --> G2
	G2 --> G3
	G2 --> G4
	G2 --> D3
	D3 --> D1
	D3 --> D2
	G4 --> S1
	G2 --> B1
	B1 --> B2
	B2 --> B3
	B3 --> S2
	S1 --> E1
	S2 --> E1
	E1 --> E2
	E2 --> E3
	E1 --> E4
	E4 --> V2
	S1 --> V1
	S2 --> V1
	E1 --> V1
```

## Determinism contract (§13 of the build spec)

- Same NL prompt + same seed + same schema version → byte-identical `stratfile.json`
  (excluding `metadata.created_at`).
- Same stratfile + same date range + same data snapshot → byte-identical backtest metrics.
- `receipt.json` content hash = `sha256(canonical_json(stratfile body))`, excluding
  `metadata` (volatile) and `description` (prose; may be LLM-polished and LLM output is
  not reproducible across model updates). Two stratfiles that trade identically hash identically.
- Tests are hermetic: no live network, fixtures only.

## Information-set discipline

Every `data_sources[*].shift` must be `<= -1` (schema-enforced). The backtest engine shifts
decision inputs by that lag; execution fills use the unshifted day-t close. A decision at
day t can never see day-t data.

## Failure-mode design

| Dependency | Failure handling |
| --- | --- |
| CMC MCP | Circuit breaker (3 strikes → 120s open) → REST fallback |
| CMC REST | Missing key / 429 / 5xx → DuckDB cache → checked-in fixtures |
| x402 on Base | No funded wallet / unreachable → deterministic stub proof, clearly labeled `mode: stub` |
| bnbagent-sdk | Not importable (unpublished on PyPI) → direct web3.py call → dry-run identity |
| BSC RPC | 3 RPCs tried in order → `--dry-run` keeps the demo alive |
| MegaFuel | Sponsored send fails → normal gas with faucet BNB |
