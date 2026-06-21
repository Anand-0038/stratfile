"""End-to-end: the full §8 API surface — upload -> evaluate -> dry-run execute.

Runs against the FastAPI app in-process (hermetic, no docker needed in CI).
If STRATFILE_E2E_URL is set (e.g. http://localhost:8080 with docker compose up),
the same flow runs against the live container instead.
"""

import json
import os

import httpx
import pytest

from stratfile import config

LIVE_URL = os.environ.get("STRATFILE_E2E_URL")


@pytest.fixture(scope="module")
def client():
    if LIVE_URL:
        with httpx.Client(base_url=LIVE_URL, timeout=30) as c:
            yield c
    else:
        from fastapi.testclient import TestClient

        from stratfile.executor.app import app

        with TestClient(app) as c:
            yield c


@pytest.fixture(scope="module")
def example_pair():
    root = config.repo_root() / "examples"
    strat = (root / "btc-fear-greed-dca.stratfile.json").read_bytes()
    receipt = (root / "btc-fear-greed-dca.receipt.json").read_bytes()
    return strat, receipt


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["network"] == "bsc-testnet"


def test_identity_present(client):
    r = client.get("/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["address"].startswith("0x")
    assert body["token_id"] is not None


def test_upload_evaluate_execute_flow(client, example_pair):
    strat, receipt = example_pair
    r = client.post(
        "/strategies",
        files={
            "stratfile": ("s.stratfile.json", strat, "application/json"),
            "receipt": ("s.receipt.json", receipt, "application/json"),
        },
    )
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["validation"] == "ok"
    sid = up["strategy_id"]

    r = client.get("/strategies")
    assert any(s["strategy_id"] == sid for s in r.json())

    r = client.get(f"/strategies/{sid}")
    assert r.status_code == 200
    assert r.json()["stratfile"]["name"] == "btc-fear-greed-dca"

    r = client.post(f"/strategies/{sid}/evaluate")
    assert r.status_code == 200
    ev = r.json()
    assert ev["current_regime"] in ("risk_on", "risk_off", "neutral")
    assert "signals" in ev

    r = client.post(f"/strategies/{sid}/execute", json={"dry_run": True})
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["dry_run"] is True
    assert ex["regime"] == ev["current_regime"]

    r = client.get(f"/receipts/{sid}")
    assert r.status_code == 200
    assert r.json()["stratfile"]["content_hash"].startswith("sha256:")

    r = client.get("/runs", params={"strategy_id": sid})
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_invalid_stratfile_rejected_rfc7807(client):
    bad = json.dumps({"name": "nope"}).encode()
    r = client.post(
        "/strategies", files={"stratfile": ("bad.json", bad, "application/json")}
    )
    assert r.status_code == 400
    assert "problem" in r.headers["content-type"]
    assert r.json()["title"] == "Stratfile validation failed"


def test_lookahead_stratfile_rejected(client, example_pair):
    doc = json.loads(example_pair[0])
    doc["data_sources"][0]["shift"] = 0
    r = client.post(
        "/strategies",
        files={"stratfile": ("la.json", json.dumps(doc).encode(), "application/json")},
    )
    assert r.status_code == 400
    assert "shift" in r.json()["detail"]


def test_metrics_prometheus_format(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "stratfile_requests_total" in r.text
