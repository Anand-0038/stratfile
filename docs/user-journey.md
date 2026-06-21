# User journeys

Two sides, one artifact.

## Side A — Author (quant dev in Claude Code / Cursor / terminal)

```mermaid
journey
	title Author: idea to signed artifact
	section Author
	  Write English idea: 5: Author
	  stratfile init: 5: Author
	  stratfile validate: 4: Author
	  stratfile backtest: 4: Author
	  stratfile receipt: 5: Author
	  Share stratfile.json + receipt.json: 5: Author
```

```mermaid
flowchart LR
	A["English idea"] --> B["stratfile init"]
	B --> C{"valid?"}
	C -->|no| D["clear error, fail closed"]
	C -->|yes| E["stratfile.json"]
	E --> F["stratfile backtest<br>walk-forward, regime table"]
	F --> G["stratfile receipt<br>hash + x402 + signature"]
	G --> H["publish artifact pair"]
```

## Side B — Consumer (on-chain agent / auditor)

```mermaid
flowchart LR
	H["artifact pair"] --> I["POST /strategies<br>schema + invariant validation"]
	I --> J["stratfile verify-receipt<br>hash · signature · x402"]
	J --> K["POST /strategies/id/evaluate<br>current regime, no tx"]
	K --> L{"act?"}
	L -->|dry run| M["simulated swap<br>identical call path"]
	L -->|live| N["PancakeSwap V3 Testnet swap<br>ERC-8004 identity, MegaFuel gas"]
	N --> O["BscScan Testnet link<br>in dashboard + /runs"]
```

The consumer never trusts the author: the receipt is verified by recomputing the content
hash, recovering the ECDSA signer, and checking the x402 payment proof.
