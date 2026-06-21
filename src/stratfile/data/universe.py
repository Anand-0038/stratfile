"""Token universe with listing status preserved (survivorship-bias guard, risk #5).

Never hard-code the competition allowlist (§17) — read the CMC universe at runtime
when keys are present, fall back to the checked-in snapshot otherwise.
"""

from __future__ import annotations

import json
import logging

from .. import config
from . import cache, cmc_rest

log = logging.getLogger("stratfile.universe")

_FIXTURE = "universe_snapshot.json"


def get_universe() -> list[dict]:
    """[{symbol, name, is_active}] — delisted entries kept with is_active=0."""
    cached = cache.cache_get("universe", max_age_s=24 * 3600)
    if cached is not None:
        return cached["tokens"]
    try:
        raw = cmc_rest.crypto_map(limit=500)
        tokens = [
            {"symbol": t["symbol"], "name": t["name"], "is_active": int(t.get("is_active", 1))}
            for t in raw
        ]
        cache.cache_put("universe", {"tokens": tokens})
        return tokens
    except cmc_rest.RestUnavailable:
        path = config.fixtures_dir() / _FIXTURE
        if path.is_file():
            return json.loads(path.read_text())["tokens"]
        log.warning("No universe snapshot fixture; falling back to parser token list")
        from ..generator.parser import KNOWN_TOKENS

        return [{"symbol": s, "name": s, "is_active": 1} for s in KNOWN_TOKENS]


def is_supported(symbol: str) -> bool:
    return any(t["symbol"] == symbol.upper() for t in get_universe())
