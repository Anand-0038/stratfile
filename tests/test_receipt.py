"""Receipt: content hash matches, signature verifies, stub proof is deterministic."""

import json

from stratfile import config
from stratfile.backtest import engine, receipt as receipt_mod
from stratfile.canonical import content_hash
from stratfile.x402 import client as x402_client
from stratfile.x402.proof import verify_receipt


def _example() -> dict:
    path = config.repo_root() / "examples" / "btc-fear-greed-dca.stratfile.json"
    return json.loads(path.read_text())


def _receipt():
    doc = _example()
    result = engine.run(doc, windows=10)
    return doc, receipt_mod.build_receipt(doc, result)


def test_receipt_verifies_end_to_end():
    doc, rec = _receipt()
    outcome = verify_receipt(rec, doc)
    assert outcome["ok"], outcome


def test_content_hash_matches():
    doc, rec = _receipt()
    assert rec["stratfile"]["content_hash"] == content_hash(doc)


def test_tampered_stratfile_fails_verification():
    doc, rec = _receipt()
    doc["rules"][0]["size_pct"] = 99  # tamper after signing
    outcome = verify_receipt(rec, doc)
    assert not outcome["checks"]["content_hash"]["ok"]


def test_tampered_receipt_fails_signature():
    doc, rec = _receipt()
    rec["backtest"]["metrics"]["sharpe"] = 9.99  # forge the metrics
    outcome = verify_receipt(rec, doc)
    assert not outcome["checks"]["signature"]["ok"]


def test_stub_proof_is_deterministic():
    payload = {"content_hash": "sha256:abc", "backtest_from": "2025-01-01"}
    a = x402_client.stub_proof(payload)
    b = x402_client.stub_proof(payload)
    assert a["payment_hash"] == b["payment_hash"]
    assert a["payment_hash"].startswith("stub:")


def test_backtest_is_deterministic():
    doc = _example()
    r1 = engine.run(doc, windows=10)
    r2 = engine.run(doc, windows=10)
    assert r1["metrics"] == r2["metrics"]
    assert r1["walk_forward"] == r2["walk_forward"]


def test_walkforward_windows_disjoint():
    doc = _example()
    result = engine.run(doc, windows=10)
    windows = result["walk_forward"]
    for prev, cur in zip(windows, windows[1:]):
        assert prev["to"] <= cur["from"], "walk-forward windows must not overlap"
