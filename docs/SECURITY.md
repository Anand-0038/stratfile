# Security

## Threat model

| Threat | Mitigation |
| --- | --- |
| Forged backtest results | Receipt binds metrics to the stratfile content hash with an ECDSA signature; verification recomputes both. |
| Tampered stratfile after signing | `verify-receipt` recomputes `sha256(canonical_json(stratfile minus metadata/description))` — any change to decision-relevant fields breaks the hash. |
| LLM drift poisoning reproducibility | The LLM is opt-in (`--llm-describe`), writes only the prose `description` field, and that field is excluded from the content hash — OpenAI model updates cannot invalidate receipts. |
| Look-ahead bias smuggled into a spec | Schema rejects `shift > -1`; the validator and the walk-forward tests enforce it independently. |
| Malicious regime conditions (code injection) | Conditions are tokenized against a strict grammar (identifiers, `!`, `&&`, `||`, parens) and evaluated with an empty builtins namespace over booleans only. |
| Agent goes rogue on-chain | Mandatory `guardrails`: max drawdown (schema-capped at 25%), per-trade cap, daily turnover cap, slippage cap, cooldown, kill-switch target. Executor is testnet-locked in v0.1.0. |
| Key leakage | `.env` is git-ignored; `.env.example` ships placeholders only. The deterministic demo key is used exclusively for offline/dry-run paths and is publicly known by design — never fund it. |

## Secret handling

- `EXECUTOR_PRIVATE_KEY` must be a **testnet-only** wallet. The executor refuses any
  network other than `bsc-testnet` (schema constant).
- `X402_WALLET_PRIVATE_KEY` holds only the few USDC cents needed for data payments.
- No secrets are logged; tx signing happens in-process via eth-account.

## Reporting

Open a GitHub issue or contact the author. This is hackathon software — do not use with
mainnet funds.
