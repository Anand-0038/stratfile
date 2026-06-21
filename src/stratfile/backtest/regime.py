"""Regime condition evaluation + stratification.

Conditions are boolean expressions over signal IDs using !, &&, ||, parentheses.
They are validated by generator.validator before they ever reach this module.
"""

from __future__ import annotations

import re

import pandas as pd

from ..generator.validator import parse_condition_identifiers


def _to_python(condition: str) -> str:
    expr = condition.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    return expr


def evaluate_condition(condition: str, signal_values: dict[str, bool]) -> bool:
    idents = parse_condition_identifiers(condition)
    missing = idents - signal_values.keys()
    if missing:
        raise ValueError(f"condition references unknown signals: {sorted(missing)}")
    # Safe: identifiers validated against ^[A-Za-z_][A-Za-z0-9_]*$, namespace is bools only.
    return bool(eval(_to_python(condition), {"__builtins__": {}}, dict(signal_values)))  # noqa: S307


def compute_signals(stratfile: dict, sources: dict[str, pd.Series]) -> pd.DataFrame:
    """Boolean signal matrix indexed by date. Sources are ALREADY shifted (no look-ahead)."""
    out = pd.DataFrame(index=_common_index(sources))
    for sig in stratfile["signals"]:
        series = sources[sig["source_ref"]].reindex(out.index)
        params = sig.get("params", {})
        if sig["type"] == "threshold":
            op, value = params["op"], float(params["value"])
            if op == "lt":
                out[sig["id"]] = series < value
            elif op == "gt":
                out[sig["id"]] = series > value
            elif op == "lte":
                out[sig["id"]] = series <= value
            elif op == "gte":
                out[sig["id"]] = series >= value
            else:
                raise ValueError(f"unknown threshold op: {op}")
        elif sig["type"] == "zscore":
            window = int(params.get("window", 30))
            z = (series - series.rolling(window).mean()) / series.rolling(window).std()
            out[sig["id"]] = z.abs() > float(params.get("value", 2.0))
        elif sig["type"] == "crossover":
            fast = series.rolling(int(params.get("fast", 7))).mean()
            slow = series.rolling(int(params.get("slow", 30))).mean()
            out[sig["id"]] = fast > slow
        elif sig["type"] == "regime_label":
            out[sig["id"]] = series.astype(bool)
        else:
            raise ValueError(f"unknown signal type: {sig['type']}")
    return out.fillna(False)


def compute_regimes(stratfile: dict, signals: pd.DataFrame) -> pd.Series:
    """Active regime id per day — first regime (in spec order) whose condition is true."""
    regime_ids: list[str] = []
    for _, row in signals.iterrows():
        values = {k: bool(v) for k, v in row.items()}
        active = None
        for regime in stratfile["regimes"]:
            if evaluate_condition(regime["condition"], values):
                active = regime["id"]
                break
        regime_ids.append(active or "none")
    return pd.Series(regime_ids, index=signals.index, name="regime")


def stratify_returns(returns: pd.Series, regimes: pd.Series) -> pd.DataFrame:
    """Per-regime return breakdown table."""
    df = pd.DataFrame({"ret": returns, "regime": regimes.reindex(returns.index)}).dropna()
    rows = []
    for regime, grp in df.groupby("regime"):
        rows.append(
            {
                "regime": regime,
                "days": int(len(grp)),
                "total_return_pct": round(float(((1 + grp["ret"]).prod() - 1) * 100), 4),
                "mean_daily_ret_pct": round(float(grp["ret"].mean() * 100), 4),
                "worst_day_pct": round(float(grp["ret"].min() * 100), 4),
            }
        )
    return pd.DataFrame(rows).sort_values("regime").reset_index(drop=True)


def _common_index(sources: dict[str, pd.Series]) -> pd.DatetimeIndex:
    idx: pd.DatetimeIndex | None = None
    for s in sources.values():
        idx = s.index if idx is None else idx.intersection(s.index)
    if idx is None or len(idx) == 0:
        raise ValueError("no overlapping dates across data sources")
    return idx.sort_values()


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
