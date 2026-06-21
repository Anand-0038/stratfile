"""Schema validation + the §4 hard invariants. Reject malformed stratfiles loudly."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from .. import config

_CONDITION_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||!|\(|\)|\s+")


class ValidationError(ValueError):
    pass


def load_schema() -> dict:
    return json.loads(config.schema_path().read_text())


def parse_condition_identifiers(condition: str) -> set[str]:
    """Tokenize a boolean condition; raise if anything but identifiers/!/&&/||/() present."""
    pos = 0
    idents: set[str] = set()
    while pos < len(condition):
        m = _CONDITION_TOKEN.match(condition, pos)
        if not m:
            raise ValidationError(
                f"Invalid character in regime condition at position {pos}: {condition!r}"
            )
        tok = m.group(0)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
            idents.add(tok)
        pos = m.end()
    return idents


def validate(stratfile: dict) -> list[str]:
    """Full validation. Returns list of human-readable problems; empty list = valid."""
    problems: list[str] = []

    validator = jsonschema.Draft202012Validator(load_schema())
    for err in sorted(validator.iter_errors(stratfile), key=str):
        problems.append(f"schema: {'/'.join(str(p) for p in err.absolute_path) or '$'}: {err.message}")
    if problems:
        return problems  # invariants assume schema shape

    # Invariant: no look-ahead, ever.
    for ds in stratfile["data_sources"]:
        if ds["shift"] > -1:
            problems.append(f"invariant: data_sources[{ds['id']}].shift must be <= -1 (no look-ahead)")

    # Invariant: drawdown guardrail inside the DQ gate.
    if stratfile["guardrails"]["max_drawdown_pct"] > 25:
        problems.append("invariant: guardrails.max_drawdown_pct must be <= 25 in v0.1.0")

    # Invariant: testnet only.
    if stratfile["execution"]["network"] != "bsc-testnet":
        problems.append('invariant: execution.network must be "bsc-testnet" in v0.1.0')

    ds_ids = {ds["id"] for ds in stratfile["data_sources"]}
    sig_ids = {s["id"] for s in stratfile["signals"]}
    regime_ids = {r["id"] for r in stratfile["regimes"]}

    for sig in stratfile["signals"]:
        if sig["source_ref"] not in ds_ids:
            problems.append(
                f"invariant: signals[{sig['id']}].source_ref '{sig['source_ref']}' "
                "does not reference any data_sources[*].id"
            )

    for regime in stratfile["regimes"]:
        try:
            idents = parse_condition_identifiers(regime["condition"])
        except ValidationError as exc:
            problems.append(f"invariant: regimes[{regime['id']}]: {exc}")
            continue
        unknown = idents - sig_ids
        if unknown:
            problems.append(
                f"invariant: regimes[{regime['id']}].condition references undefined "
                f"signals: {sorted(unknown)}"
            )

    for i, rule in enumerate(stratfile["rules"]):
        if rule["when_regime"] not in regime_ids:
            problems.append(
                f"invariant: rules[{i}].when_regime '{rule['when_regime']}' "
                "does not reference any regimes[*].id"
            )

    return problems


def validate_or_raise(stratfile: dict) -> None:
    problems = validate(stratfile)
    if problems:
        raise ValidationError("Stratfile validation failed:\n  - " + "\n  - ".join(problems))


def validate_file(path: str | Path) -> list[str]:
    try:
        doc = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        return [f"json: {exc}"]
    return validate(doc)
