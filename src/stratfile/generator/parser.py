"""NL prompt -> intermediate AST. Pure rules, zero LLM, fully deterministic.

The parser recognizes a constrained-but-useful grammar:
  - token symbols          "BNB", "into ETH", "rotate to USDT"
  - fear & greed source    "Fear & Greed", "fear and greed", "FNG"
  - thresholds             "< 30", "above 75", "drops below 20"
  - actions                "DCA"/"buy"/"accumulate", "rotate to X"/"exit to X"/"sell into X", "hold"

Anything unrecognized fails closed with a clear error (§ Build Spec preamble).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KNOWN_TOKENS = [
    "BNB", "BTC", "ETH", "USDT", "USDC", "BUSD", "CAKE", "SOL", "XRP", "ADA",
    "DOGE", "AVAX", "ATOM", "FIL", "FET", "INJ", "PENDLE", "AAVE", "LDO",
    "SUSHI", "1INCH", "ZETA", "AXS", "FLOKI", "SHIB", "BONK", "KAVA", "ROSE",
    "ZIL", "YFI", "COMP", "SNX", "BAT", "APE",
]

_FNG_RE = re.compile(r"fear\s*(?:&|and)?\s*greed|\bfng\b", re.IGNORECASE)
_LT_RE = re.compile(r"(?:<|below|under|less\s+than|drops?\s+below)\s*=?\s*(\d{1,3})", re.IGNORECASE)
_GT_RE = re.compile(r"(?:>|above|over|greater\s+than|exceeds?)\s*=?\s*(\d{1,3})", re.IGNORECASE)
_BUY_RE = re.compile(r"\b(dca|buy|accumulate|stack)\b", re.IGNORECASE)
_ROTATE_RE = re.compile(
    r"\b(?:rotate|exit|sell)\s+(?:in)?to\s+([A-Za-z0-9]{2,10})\b", re.IGNORECASE
)
_SIZE_RE = re.compile(r"(\d{1,3})\s*%", re.IGNORECASE)


class ParseError(ValueError):
    """Raised when the prompt cannot be parsed deterministically. Fail closed."""


@dataclass
class Threshold:
    op: str  # "lt" | "gt"
    value: int


@dataclass
class StrategyAST:
    prompt: str
    tokens: list[str] = field(default_factory=list)
    primary: str = ""
    rotate_target: str = ""
    uses_fng: bool = False
    buy_threshold: Threshold | None = None
    rotate_threshold: Threshold | None = None
    size_pct: float = 5.0


def parse(prompt: str) -> StrategyAST:
    text = prompt.strip()
    if not text:
        raise ParseError("Empty prompt.")

    ast = StrategyAST(prompt=text)

    # Token symbols, in order of first appearance, deduplicated.
    upper = text.upper()
    found: list[str] = []
    for tok in KNOWN_TOKENS:
        m = re.search(rf"\b{re.escape(tok)}\b", upper)
        if m:
            found.append((m.start(), tok))  # type: ignore[arg-type]
    found.sort()
    ast.tokens = [t for _, t in found]  # type: ignore[misc]

    if not ast.tokens:
        raise ParseError(
            f"No recognized token symbol in prompt. Known symbols: {', '.join(KNOWN_TOKENS)}"
        )

    # Data source.
    ast.uses_fng = bool(_FNG_RE.search(text))
    if not ast.uses_fng:
        raise ParseError(
            "v0.1.0 grammar requires a Fear & Greed condition "
            "(e.g. 'when Fear & Greed < 30'). No other data source recognized yet."
        )

    # Thresholds.
    if m := _LT_RE.search(text):
        ast.buy_threshold = Threshold("lt", int(m.group(1)))
    if m := _GT_RE.search(text):
        ast.rotate_threshold = Threshold("gt", int(m.group(1)))
    if ast.buy_threshold is None and ast.rotate_threshold is None:
        raise ParseError("No threshold found (e.g. '< 30' or 'above 75').")

    # Primary buy target: first non-stable token, else first token.
    stables = {"USDT", "USDC", "BUSD"}
    non_stable = [t for t in ast.tokens if t not in stables]
    ast.primary = non_stable[0] if non_stable else ast.tokens[0]

    # Rotate target.
    if m := _ROTATE_RE.search(text):
        cand = m.group(1).upper()
        if cand in KNOWN_TOKENS:
            ast.rotate_target = cand
    if not ast.rotate_target:
        stab = [t for t in ast.tokens if t in stables]
        ast.rotate_target = stab[0] if stab else "USDT"

    # DCA size.
    if m := _SIZE_RE.search(text):
        val = float(m.group(1))
        # Threshold numbers also match "N %"-less digits; _SIZE_RE requires the % sign,
        # so any hit here is an explicit size.
        if 0 < val <= 100:
            ast.size_pct = val

    if not _BUY_RE.search(text) and ast.buy_threshold is None:
        raise ParseError("No buy/DCA action recognized.")

    return ast
