# Roadmap

## 30 days — harden the format

- Publish `stratfile.schema.json` v0.1.x at `stratfile.org/schemas` (immutable URLs).
- Expand the `init` grammar: funding rates, RSI/EMA crossovers, narrative rotation signals
  (all CMC MCP tools).
- Live x402 handshake on by default with a funded facilitator account; receipt `mode: live`.
- ERC-8004 registration against the canonical BSC Testnet registry; identity card with
  BscScan-verified link.

## 60 days — two-sided network

- **Stratfile registry**: public index of published stratfiles keyed by content hash,
  SLSA-style transparency log (Rekor-inspired).
- Receipt v0.2: Reclaim-style zkTLS attestation of the CMC data fetch — proves the backtest
  inputs without revealing API keys.
- Executor adapters: Hummingbot bridge (stratfile → V2 controller config), Freqtrade export.
- Multi-strategy portfolios: `parent_stratfile` composition.

## 90 days — marketplace

- Strategy marketplace with author revenue share (Kryll model, open format instead of
  closed DSL).
- ERC-8183 strategy-as-a-service listings: agents subscribe to regime signals from
  registered stratfiles, optimistic settlement.
- Mainnet execution behind an audited guardrail module — only after external review.
- Timed stratfile competitions (Numerai-style rounds, publicly verifiable receipts instead
  of hidden scoring).

## v0.2.0 — additive-only widening (post-hackathon)

The v0.1.0 schema is a **reference profile**, not the ceiling. v0.2.0 widens
every enum behind the same field names so v0.1.0 documents stay valid forever.

### Signals
- `confidence` (0.0–1.0, optional) and `weight` (0.0–1.0, optional) fields on
  every `signals[*]` entry. Composable weighted-vote regime detection.
- New signal types: `rsi`, `ema_crossover` (named variant of `crossover`),
  `volume_spike`, `funding_rate_extreme`. All shipped today via the generic
  `threshold`/`crossover`/`zscore` types are forward-compatible.

### Data sources
- Funding-rate, open-interest, and 24h-volume tools via CMC derivatives
  endpoints + Binance public klines fallback.
- Multi-provider registry: `provider` enum widens to `cmc | binance |
  coingecko` (additive).

### Execution
- `network` widens to `bsc-mainnet` (post-audit), `base`, `arbitrum`.
- ERC-8183 Agentic Commerce: agents subscribe to strategy signals via the
  AgenticCommerce kernel; settlement on the OptimisticPolicy contract.
- `dex` and `paymaster` registries widen additively.

### Authoring
- Parser/composer NL grammar expansion to cover all v0.1.0 signal types
  (RSI thresholds, EMA crossovers, z-score outliers, multi-signal regimes).
  v0.1.0 grammar is FNG-only by design — hand-authored examples cover the
  other signal types until v0.2.
