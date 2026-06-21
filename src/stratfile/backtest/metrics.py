"""Performance metrics: Sharpe, Sortino, max drawdown, Calmar."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

ANNUALIZATION = math.sqrt(365)


def sharpe(returns: pd.Series) -> float:
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * ANNUALIZATION)


def sortino(returns: pd.Series) -> float:
    downside = returns[returns < 0]
    if len(returns) < 2 or len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(returns.mean() / downside.std() * ANNUALIZATION)


def max_drawdown_pct(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(abs(dd.min()) * 100)


def calmar(returns: pd.Series, equity: pd.Series) -> float:
    mdd = max_drawdown_pct(equity)
    if mdd == 0 or len(equity) < 2:
        return 0.0
    days = len(returns)
    total = float(equity.iloc[-1] / equity.iloc[0])
    annual_ret = total ** (365 / days) - 1 if days > 0 else 0.0
    return float(annual_ret / (mdd / 100))


def summarize(equity: pd.Series, n_trades: int) -> dict:
    returns = equity.pct_change().dropna()
    total_return_pct = float((equity.iloc[-1] / equity.iloc[0] - 1) * 100)
    return {
        "total_return_pct": round(total_return_pct, 4),
        "sharpe": round(sharpe(returns), 4),
        "sortino": round(sortino(returns), 4),
        "max_drawdown_pct": round(max_drawdown_pct(equity), 4),
        "calmar": round(calmar(returns, equity), 4),
        "n_trades": int(n_trades),
        "n_days": int(len(equity)),
        "start_equity": float(equity.iloc[0]),
        "end_equity": round(float(equity.iloc[-1]), 2),
    }


def buy_and_hold_equity(price: pd.Series, start_cash: float) -> pd.Series:
    qty = start_cash / float(price.iloc[0])
    return price * qty


def monte_carlo_drawdown(returns: pd.Series, n_paths: int = 500, seed: int = 42) -> dict:
    """Bootstrap resampling of daily returns — overfitting guard (risk #3)."""
    rng = np.random.default_rng(seed)
    vals = returns.to_numpy()
    if len(vals) == 0:
        return {"p50_max_dd_pct": 0.0, "p95_max_dd_pct": 0.0}
    dds = []
    for _ in range(n_paths):
        sample = rng.choice(vals, size=len(vals), replace=True)
        eq = np.cumprod(1 + sample)
        peak = np.maximum.accumulate(eq)
        dds.append(abs(((eq - peak) / peak).min()) * 100)
    return {
        "p50_max_dd_pct": round(float(np.percentile(dds, 50)), 4),
        "p95_max_dd_pct": round(float(np.percentile(dds, 95)), 4),
    }
