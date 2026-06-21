"""Every example validates against the pinned schema; invariants are enforced."""

import json
from pathlib import Path

import pytest

from stratfile import config
from stratfile.generator import validator

EXAMPLES = sorted((config.repo_root() / "examples").glob("*.stratfile.json"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.name for p in EXAMPLES])
def test_example_validates(path: Path):
    problems = validator.validate_file(path)
    assert problems == [], f"{path.name}: {problems}"


def test_schema_is_pinned_v010():
    schema = json.loads(config.schema_path().read_text())
    assert schema["$id"] == "https://stratfile.org/schemas/v0.1.0/stratfile.schema.json"
    assert schema["properties"]["version"]["const"] == "0.1.0"
    assert schema["properties"]["execution"] is not None


def test_lookahead_rejected():
    doc = json.loads(EXAMPLES[0].read_text())
    doc["data_sources"][0]["shift"] = 0  # look-ahead!
    problems = validator.validate(doc)
    assert any("shift" in p for p in problems)


def test_mainnet_rejected():
    doc = json.loads(EXAMPLES[0].read_text())
    doc["execution"]["network"] = "bsc-mainnet"
    problems = validator.validate(doc)
    assert problems, "mainnet must be rejected in v0.1.0"


def test_drawdown_cap_rejected():
    doc = json.loads(EXAMPLES[0].read_text())
    doc["guardrails"]["max_drawdown_pct"] = 30
    problems = validator.validate(doc)
    assert problems, "max_drawdown_pct > 25 must be rejected"


def test_dangling_refs_rejected():
    doc = json.loads(EXAMPLES[0].read_text())
    doc["signals"][0]["source_ref"] = "nonexistent"
    problems = validator.validate(doc)
    assert any("source_ref" in p for p in problems)

    doc = json.loads(EXAMPLES[0].read_text())
    doc["rules"][0]["when_regime"] = "nonexistent"
    problems = validator.validate(doc)
    assert any("when_regime" in p for p in problems)

    doc = json.loads(EXAMPLES[0].read_text())
    doc["regimes"][0]["condition"] = "undefined_signal"
    problems = validator.validate(doc)
    assert any("undefined" in p for p in problems)
