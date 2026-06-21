# Stratfile

**An on-chain trading agent with a portable strategy spec.** Stratfile is a
`Dockerfile`-like open file format for trading strategies — one deterministic
JSON artifact authored in English, backtested with walk-forward + signed
receipts, and executed on BSC Testnet by an **ERC-8004-registered agent**.
Same file. Two consumers. Verifiable by anyone. No trusted server.

```text
English idea ──> stratfile.json ──> walk-forward backtest ──> receipt.json ──> ERC-8004 agent ──> PancakeSwap V3 Testnet
                 (deterministic)     (34 windows, no look-ahead)  (signed, verifiable)  (gas-free via MegaFuel)
```

## 30-second quickstart

```bash
docker compose up --build
# Executor  -> http://localhost:8080/health
# Dashboard -> http://localhost:8501
```

Pick a strategy. All four backtest with the same engine and execute on
BSC Testnet with the same ERC-8004 identity:

| Strategy | Signal type | What it shows |
|---|---|---|
| `examples/bnb-ema-crossover.stratfile.json` (default) | `crossover` | Schema handles technical indicators |
| `examples/bnb-mean-reversion.stratfile.json` | `zscore` | Schema handles statistical outliers |
| `examples/eth-fear-greed-dca.stratfile.json` | `threshold` | Schema is asset-agnostic |
| `examples/btc-fear-greed-dca.stratfile.json` | `threshold` | Honest baseline — kill-switch fires on −22% draw |

```bash
pip install -e .

F=examples/bnb-ema-crossover.stratfile.json
stratfile validate  $F
stratfile backtest  $F --windows 34
stratfile receipt   $F
stratfile verify-receipt $F examples/bnb-ema-crossover.receipt.json
stratfile execute   $F --dry-run
```

## Live on-chain proof (BSC Testnet)

**ERC-8004 Identity Registration (successful)**  
https://testnet.bscscan.com/tx/0x659d95abd0e4b99e9dca954fb0ff4dc54f2b96a71fd8dda408ee38f794d2905f  
Token ID: 744973 — registered on the canonical `bnb-chain/erc-8004-contracts` registry.

**Execution path verified**  
The executor correctly constructs the `exactInputSingle` payload for PancakeSwap V3.  
Live token swaps require testnet ERC-20 provisioning (USDT/WBNB) which is environment-specific.  
The same payload is produced deterministically with `--dry-run` for full reproducibility.

`docker compose up` reproduces the complete proof loop locally.

Everything works **offline** out of the box (checked-in real historical fixtures + deterministic
proof stubs). Populate `.env` (see `.env.example`) to switch on live CMC MCP data, real x402
payments on Base, and real BSC Testnet transactions.

## The 90-second proof loop

1. **Generate** — `stratfile init "<English>"` emits a deterministic, schema-validated `stratfile.json`.
   Same prompt + same seed = byte-identical bytes. Rule-based composer; no LLM in the decision loop.
2. **Backtest** — 34-window walk-forward on the held-out range, all signals shifted to t-1
   (look-ahead is rejected by the schema: `data_sources[*].shift <= -1`). Regime-stratified table,
   Sharpe / Sortino / max-DD / Calmar, Monte-Carlo bootstrap drawdown.
3. **Receipt** — `receipt.json` binds the content hash to the backtest with an ECDSA signature and
   an x402 payment proof (USDC on Base; deterministic stub when offline).
4. **Execute** — the FastAPI executor loads the same file, registers an **ERC-8004 identity** on
   BSC Testnet (gas-free via MegaFuel paymaster), and fires `exactInputSingle` on PancakeSwap V3
   Testnet — or simulates the identical call path with `--dry-run`.
5. **Reproduce** — `docker compose up` replays all of the above on your machine.

## v0.1.0 is a reference profile, not the ceiling

The schema locks `provider=cmc`, `network=bsc-testnet`, `dex=pancakeswap-v3-testnet`,
`paymaster=megafuel` for v0.1.0. That is deliberate: testnet-only is a **safety invariant**,
and the single-provider profile keeps every v0.1.0 artifact runnable by every v0.1.0
executor. The format itself is venue-agnostic — v0.2.0 widens each enum additively
(documented in the schema `description` fields and [docs/ROADMAP.md](docs/ROADMAP.md))
without breaking a single v0.1.0 document. Hummingbot's V1→V2 config rewrite is the
anti-pattern we're avoiding.

## Why this is different

- **Freqtrade / QuantConnect** ship strategies as Python code — not portable across executors or agents.
- **Hummingbot V2** ships YAML strategy configs — Stratfile is the same idea, JSON-Schema-validated,
  signed, and on-chain-consumable.
- **Composer** turns English into strategies — but closed-source, equities-only, no receipts.
- **Nobody** binds a strategy spec to a verifiable backtest receipt and an ERC-8004 on-chain identity.

## The artifact pair

| File | What it proves |
| --- | --- |
| `stratfile.json` | The strategy: universe, data sources (with mandatory information lag), signals, regimes, rules, guardrails, execution target. Pinned schema `v0.1.0`. |
| `receipt.json` | The evidence: `sha256(canonical_json(stratfile))`, walk-forward metrics, x402 payment proof, ECDSA signature. `stratfile verify-receipt` checks all three. |

## Architecture

```text
          ┌────────────── Author CLI (stratfile) ──────────────┐
 English ─┤ parser -> composer -> validator    backtester      │
          │     (deterministic, rule-based)    (walk-forward)  │
          └───────┬──────────────────────────────┬─────────────┘
                  │ stratfile.json               │ receipt.json (signed)
                  ▼                              ▼
          ┌─────────────── Executor (FastAPI :8080) ───────────┐
          │ ERC-8004 identity · MegaFuel paymaster · PancakeSwap│
          │ V3 Testnet swaps · /evaluate · /execute (dry_run)   │
          └───────┬─────────────────────────────────────────────┘
                  ▼
          Dashboard (Streamlit :8501) — spec · backtest · receipt · identity · last tx
```

Data path: **CMC MCP** (`mcp.coinmarketcap.com`, circuit-breaker) → **CMC REST** fallback →
**DuckDB cache + checked-in fixtures** (token-bucket rate limiting throughout).

## Guardrails so the agent can't go rogue

Every stratfile must declare `guardrails`: max drawdown (hard-capped at 25% by the schema),
per-trade cap, daily turnover cap, slippage cap, cooldown, and a kill-switch target. The
backtester enforces them; the executor clamps swaps with them.

## Repository map

```text
stratfile.schema.json      # THE contract — pinned v0.1.0, semver-locked
examples/                  # reference strategy + signed receipt
src/stratfile/generator/   # NL -> AST -> composer -> validator (deterministic)
src/stratfile/data/        # CMC MCP + REST fallback + DuckDB cache + fixtures
src/stratfile/backtest/    # walk-forward engine, regime stratifier, receipt signer
src/stratfile/x402/        # 402 -> sign -> 200 client + proof verification
src/stratfile/executor/    # FastAPI, ERC-8004 identity, PancakeSwap V3 trader, MegaFuel
src/stratfile/dashboard/   # Streamlit UI
skill/                     # CMC Skills Marketplace manifest
fixtures/                  # real historical FNG + BNB/USD data (offline demo + hermetic tests)
```

## Tests

```bash
pytest tests/   # schema validation · byte-determinism · receipt signature round-trip
```

No live network anywhere in the tests — fixtures only.

## License

MIT — see [LICENSE](LICENSE).
