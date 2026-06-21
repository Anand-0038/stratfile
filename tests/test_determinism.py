"""Same prompt + same seed = byte-identical stratfile.json (excluding metadata.created_at)."""

from stratfile.canonical import canonical_json, content_hash, strip_created_at
from stratfile.generator import composer, parser, validator

PROMPT = "DCA into BNB when Fear & Greed < 30, rotate to USDT when > 75"


def _generate(seed: int = 42) -> dict:
    ast = parser.parse(PROMPT)
    doc = composer.compose(ast, seed=seed)
    validator.validate_or_raise(doc)
    return doc


def test_byte_identical_same_seed():
    a = canonical_json(strip_created_at(_generate(seed=42)))
    b = canonical_json(strip_created_at(_generate(seed=42)))
    assert a == b


def test_content_hash_stable():
    # content_hash already excludes metadata entirely
    assert content_hash(_generate()) == content_hash(_generate())


def test_description_excluded_from_content_hash():
    """M3 guard: LLM-polished prose must never invalidate a receipt."""
    doc = _generate()
    rewritten = dict(doc, description="A completely different LLM-written description.")
    assert content_hash(doc) == content_hash(rewritten)

    # ...but decision-relevant fields DO change the hash.
    tampered = dict(doc, rules=[dict(doc["rules"][0], size_pct=99)] + doc["rules"][1:])
    assert content_hash(doc) != content_hash(tampered)


def test_content_hash_changes_with_strategy():
    doc_a = _generate()
    ast = parser.parse("DCA into ETH when Fear & Greed < 20, rotate to USDT when > 80")
    doc_b = composer.compose(ast)
    assert content_hash(doc_a) != content_hash(doc_b)


def test_parse_extracts_structure():
    ast = parser.parse(PROMPT)
    assert ast.primary == "BNB"
    assert ast.rotate_target == "USDT"
    assert ast.buy_threshold.op == "lt" and ast.buy_threshold.value == 30
    assert ast.rotate_threshold.op == "gt" and ast.rotate_threshold.value == 75


def test_unparseable_prompt_fails_closed():
    import pytest

    with pytest.raises(parser.ParseError):
        parser.parse("buy low sell high")  # no token, no source, no threshold


def test_generated_matches_reference_shape():
    doc = _generate()
    assert doc["execution"]["network"] == "bsc-testnet"
    assert all(ds["shift"] <= -1 for ds in doc["data_sources"])
    assert doc["guardrails"]["max_drawdown_pct"] <= 25
