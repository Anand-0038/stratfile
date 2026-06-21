"""Canonical JSON serialization + content hashing.

The receipt contract (§13): content hash = sha256(canonical_json(stratfile body)),
where body excludes HASH_EXCLUDED_FIELDS (metadata + description).
Canonical form: sorted keys, no whitespace, UTF-8, no ASCII escaping.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# Fields excluded from the content hash:
#  - metadata:     volatile (created_at, prompt, tags)
#  - description:  human-readable prose only; MAY be LLM-polished, and LLM output is not
#    reproducible across model updates even at temperature=0. Excluding it keeps the hash
#    pinned to the strategy SEMANTICS (universe, signals, regimes, rules, guardrails,
#    execution) — two stratfiles that trade identically hash identically.
HASH_EXCLUDED_FIELDS = ("metadata", "description")


def content_hash(stratfile: dict) -> str:
    """Hash of the stratfile's decision-relevant body (see HASH_EXCLUDED_FIELDS)."""
    body = {k: v for k, v in stratfile.items() if k not in HASH_EXCLUDED_FIELDS}
    return "sha256:" + sha256_hex(canonical_json(body))


def strip_created_at(stratfile: dict) -> dict:
    """Copy without metadata.created_at — the only field allowed to vary between
    two otherwise byte-identical generations (§13)."""
    out = json.loads(json.dumps(stratfile))
    if isinstance(out.get("metadata"), dict):
        out["metadata"].pop("created_at", None)
    return out
