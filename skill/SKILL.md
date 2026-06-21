---
name: stratfile
description: Author, validate, backtest, and execute trading strategies as portable stratfile.json artifacts. Turns a plain-English strategy idea into a deterministic, JSON-Schema-validated spec with a signed walk-forward backtest receipt (SHA-256 content hash + x402 payment proof), consumable by any ERC-8004 agent on BSC Testnet. Use when the user wants to define, verify, or run a crypto trading strategy.
---

# Stratfile Skill

Define your trading strategy in a Stratfile. Backtest it. Run it on-chain.

## What this skill does

1. **Author** — translate a natural-language strategy idea into a deterministic
   `stratfile.json` (pinned schema v0.1.0). Same prompt + same seed = byte-identical output.
2. **Validate** — JSON-Schema + hard invariants: no look-ahead (`shift <= -1`),
   max drawdown <= 25%, testnet-only execution, no dangling references.
3. **Backtest** — 34-window walk-forward with regime stratification, Sharpe/Sortino/max-DD/Calmar,
   and Monte-Carlo bootstrap drawdown. Data via CMC MCP with REST fallback and local cache.
4. **Receipt** — emit a signed `receipt.json`: content hash + x402 payment proof (USDC on Base)
   + ECDSA signature. Anyone can verify without trusting the author.
5. **Execute** — hand the same artifact to the reference executor: ERC-8004 identity on
   BSC Testnet, swaps on PancakeSwap V3 Testnet, gas-free via MegaFuel. `--dry-run` supported.

## Commands

```bash
stratfile init "DCA into BNB when Fear & Greed < 30, rotate to USDT when > 75"
stratfile validate <file>.stratfile.json
stratfile backtest <file>.stratfile.json --windows 34
stratfile receipt  <file>.stratfile.json
stratfile verify-receipt <file>.stratfile.json <file>.receipt.json
stratfile execute  <file>.stratfile.json --dry-run
```

## Grammar accepted by `init` (v0.1.0)

- Token symbols: BNB, BTC, ETH, USDT, USDC, CAKE, and other CMC-listed BEP-20 majors.
- Data source: Fear & Greed Index ("when Fear & Greed < 30", "FNG above 75").
- Actions: DCA/buy/accumulate into a token; rotate/exit/sell to a stable; hold.
- Sizes: "5%" style percentages (default 5% per DCA tranche).

Prompts outside the grammar fail closed with a clear error — the composer never guesses.

## Data sources

CMC MCP (`mcp.coinmarketcap.com`, header `X-CMC-MCP-API-KEY`) with circuit breaker,
token-bucket rate limiting, CMC REST fallback, DuckDB cache, and checked-in historical
fixtures so everything also works offline.

## Outputs

| Artifact | Contract |
| --- | --- |
| `<name>.stratfile.json` | Validates against `https://stratfile.org/schemas/v0.1.0/stratfile.schema.json` |
| `<name>.receipt.json` | `content_hash = sha256(canonical_json(stratfile minus metadata/description))`, ECDSA-signed, x402 proof attached |
