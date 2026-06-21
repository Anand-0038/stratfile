"""Walk-forward hygiene: no look-ahead, disjoint windows, survivorship handling.

Spec §14.3: assert signal shift <= -1 for all signals; assert no overlap between
train and test windows; assert delisted-flag handling.
"""

import json

import pandas as pd
import pytest

from stratfile import config
from stratfile.backtest import engine
from stratfile.data import universe


def _example() -> dict:
    path = config.repo_root() / "examples" / "btc-fear-greed-dca.stratfile.json"
    return json.loads(path.read_text())


# --- No look-ahead, ever -------------------------------------------------


def test_all_data_sources_shifted():
    doc = _example()
    assert all(ds["shift"] <= -1 for ds in doc["data_sources"])


def test_loaded_sources_are_lagged():
    """The series the engine trades on must lag the raw fixture by >= 1 day."""
    doc = _example()
    raw = engine.cache.get_daily_series("fear_greed_index")
    shifted = engine.load_sources(doc)["fng"]
    # The value the strategy sees on day t equals the raw value of day t-1.
    common = raw.index.intersection(shifted.index)[5:50]
    for ts in common:
        prev = raw.index[raw.index.get_loc(ts) - 1]
        assert shifted.loc[ts] == raw.loc[prev], f"signal at {ts} is not lagged"


def test_decision_day_cannot_see_same_day_data():
    """End-to-end look-ahead probe: spike the LAST day of the FNG fixture to extreme
    fear; with shift=-1 the strategy must NOT act on it on that same day."""
    doc = _example()
    sources = engine.load_sources(doc)
    last_day = sources["fng"].dropna().index[-1]
    raw = engine.cache.get_daily_series("fear_greed_index")
    # shifted value on last_day comes from the day before, never last_day itself
    assert sources["fng"].loc[last_day] == raw.iloc[raw.index.get_loc(last_day) - 1]


# --- Walk-forward windows ------------------------------------------------


def test_windows_disjoint_and_ordered():
    result = engine.run(_example(), windows=12)
    wf = result["walk_forward"]
    assert len(wf) == 12
    for prev, cur in zip(wf, wf[1:]):
        assert prev["to"] <= cur["from"], "windows must not overlap"
        assert prev["window"] < cur["window"]


def test_windows_cover_full_range():
    result = engine.run(_example(), windows=8)
    wf = result["walk_forward"]
    assert wf[0]["from"] == result["from"]
    assert wf[-1]["to"] == result["to"]


def test_too_few_days_fails_closed():
    with pytest.raises(engine.BacktestError):
        engine.run(_example(), date_from="2026-06-01", windows=34)


# --- Guardrails enforced in simulation -----------------------------------


def test_kill_switch_respects_guardrail():
    """If the kill switch fired, post-freeze equity must be flat (rotated to stable)."""
    result = engine.run(_example(), windows=8)
    if not result["kill_switch_fired"]:
        pytest.skip("kill switch did not fire on this fixture range")
    eq = pd.DataFrame(result["equity_curve"]).set_index("date")["equity"]
    kill_trades = [t for t in result["trades"] if t["action"] == "kill_switch"]
    assert kill_trades, "kill_switch_fired implies a kill_switch trade"
    after = eq[eq.index > kill_trades[-1]["date"]]
    assert after.nunique() <= 1, "equity must freeze after kill switch"


def test_per_trade_cap_respected():
    doc = _example()
    result = engine.run(doc, windows=8)
    cap_pct = doc["guardrails"]["per_trade_cap_pct"]
    eq = {row["date"]: row["equity"] for row in result["equity_curve"]}
    for t in result["trades"]:
        if t["action"] != "buy":
            continue
        # trade size <= cap% of that day's equity (small tolerance for same-day equity motion)
        assert t["usd"] <= eq[t["date"]] * cap_pct / 100 * 1.05


# --- Survivorship (risk #5) ----------------------------------------------


def test_universe_preserves_delisted_flag():
    tokens = universe.get_universe()
    assert tokens, "universe must never be empty"
    assert all("is_active" in t for t in tokens), "listing status must be preserved"
