"""Walk-forward backtest engine. Deterministic pandas implementation.

Information-set discipline (risks #3/#4): every data source is shifted by its
`shift` field (schema enforces <= -1), so a decision on day t only ever sees
data from day t-1 or earlier. Walk-forward = the strategy runs with fixed,
pre-registered parameters over `windows` disjoint out-of-sample segments —
there is no in-sample fitting step to leak from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from ..data import cache
from . import metrics, regime

log = logging.getLogger("stratfile.backtest")

START_CASH_USD = 10_000.0
STABLES = {"USDT", "USDC", "BUSD"}


class BacktestError(RuntimeError):
    pass


@dataclass
class _Portfolio:
    cash_usd: float
    holdings: dict[str, float] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    traded_today_usd: float = 0.0
    last_fired: dict[int, pd.Timestamp] = field(default_factory=dict)
    frozen: bool = False

    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash_usd
        for tok, qty in self.holdings.items():
            total += qty * prices.get(tok, 1.0)
        return total


def load_sources(stratfile: dict) -> dict[str, pd.Series]:
    """Load each data_source as a daily series, shifted per its `shift` field."""
    out: dict[str, pd.Series] = {}
    for ds in stratfile["data_sources"]:
        series = cache.get_daily_series(ds["tool"], ds.get("args"))
        lag = -int(ds["shift"])  # shift=-1 -> lag 1 day
        out[ds["id"]] = series.shift(lag)
    return out


def _price_series(stratfile: dict, sources: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """UNSHIFTED execution prices per token (fills happen at day-t close;
    only *decisions* use lagged data)."""
    prices: dict[str, pd.Series] = {}
    for ds in stratfile["data_sources"]:
        if ds["tool"] == "price_quote":
            sym = str(ds.get("args", {}).get("symbol", "")).upper()
            if sym:
                prices[sym] = cache.get_daily_series(ds["tool"], ds.get("args"))
    for tok in stratfile["universe"]:
        if tok in STABLES and tok not in prices:
            continue  # stables priced at 1.0
        if tok not in STABLES and tok not in prices:
            raise BacktestError(
                f"No price_quote data source for non-stable token {tok!r}. "
                "Add one to data_sources."
            )
    return prices


def run(
    stratfile: dict,
    date_from: str | None = None,
    date_to: str | None = None,
    windows: int = 34,
) -> dict:
    sources = load_sources(stratfile)
    price_series = _price_series(stratfile, sources)

    signals = regime.compute_signals(stratfile, sources)
    if date_from:
        signals = signals[signals.index >= pd.Timestamp(date_from)]
    if date_to:
        signals = signals[signals.index <= pd.Timestamp(date_to)]
    # Constrain to dates where execution prices exist.
    for ps in price_series.values():
        signals = signals[signals.index.isin(ps.index)]
    if len(signals) < windows * 2:
        raise BacktestError(
            f"Only {len(signals)} usable days for {windows} windows. "
            "Widen the date range or reduce --windows."
        )

    regimes = regime.compute_regimes(stratfile, signals)
    guard = stratfile["guardrails"]
    rules = stratfile["rules"]

    pf = _Portfolio(cash_usd=START_CASH_USD)
    slip = guard["slippage_cap_bps"] / 2 / 10_000  # half the cap as deterministic cost
    equity_values: list[float] = []
    peak = START_CASH_USD
    kill_switch_fired = False

    for ts in signals.index:
        day_prices = {tok: float(ps.loc[ts]) for tok, ps in price_series.items()}
        for stable in STABLES:
            day_prices.setdefault(stable, 1.0)
        pf.traded_today_usd = 0.0

        active = regimes.loc[ts]
        equity_now = pf.equity(day_prices)
        peak = max(peak, equity_now)
        drawdown_pct = (peak - equity_now) / peak * 100

        if not pf.frozen and drawdown_pct > guard["max_drawdown_pct"]:
            _rotate_all(pf, guard["kill_switch_target"], day_prices, slip, ts, reason="kill_switch")
            pf.frozen = True
            kill_switch_fired = True

        if not pf.frozen:
            for i, rule in enumerate(rules):
                if rule["when_regime"] != active:
                    continue
                cooldown_h = float(rule.get("cooldown_h", 0))
                last = pf.last_fired.get(i)
                if last is not None and (ts - last) < pd.Timedelta(hours=cooldown_h):
                    continue
                if _apply_rule(pf, rule, day_prices, guard, slip, ts):
                    pf.last_fired[i] = ts

        equity_values.append(pf.equity(day_prices))

    equity = pd.Series(equity_values, index=signals.index, name="equity")
    returns = equity.pct_change().dropna()

    # Walk-forward: disjoint contiguous out-of-sample windows.
    wf_rows = []
    bounds = [int(round(i * len(equity) / windows)) for i in range(windows + 1)]
    for w in range(windows):
        seg = equity.iloc[bounds[w] : bounds[w + 1]]
        if len(seg) < 2:
            continue
        seg_ret = seg.pct_change().dropna()
        wf_rows.append(
            {
                "window": w + 1,
                "from": seg.index[0].date().isoformat(),
                "to": seg.index[-1].date().isoformat(),
                "return_pct": round(float((seg.iloc[-1] / seg.iloc[0] - 1) * 100), 4),
                "sharpe": round(metrics.sharpe(seg_ret), 4),
                "max_dd_pct": round(metrics.max_drawdown_pct(seg), 4),
            }
        )

    primary = next((t for t in stratfile["universe"] if t not in STABLES), None)
    benchmark = None
    if primary and primary in price_series:
        ps = price_series[primary].reindex(equity.index).dropna()
        bh = metrics.buy_and_hold_equity(ps, START_CASH_USD)
        benchmark = {
            "token": primary,
            "buy_hold_return_pct": round(float((bh.iloc[-1] / bh.iloc[0] - 1) * 100), 4),
        }

    breakdown = regime.stratify_returns(returns, regimes)

    return {
        "from": equity.index[0].date().isoformat(),
        "to": equity.index[-1].date().isoformat(),
        "windows": windows,
        "metrics": metrics.summarize(equity, n_trades=len(pf.trades)),
        "walk_forward": wf_rows,
        "regime_breakdown": breakdown.to_dict(orient="records"),
        "monte_carlo": metrics.monte_carlo_drawdown(returns),
        "benchmark": benchmark,
        "kill_switch_fired": kill_switch_fired,
        "trades": pf.trades[-200:],
        "equity_curve": [
            {"date": ts.date().isoformat(), "equity": round(v, 2)}
            for ts, v in equity.items()
        ],
        "regime_series": [
            {"date": ts.date().isoformat(), "regime": r} for ts, r in regimes.items()
        ],
    }


def _apply_rule(
    pf: _Portfolio,
    rule: dict,
    prices: dict[str, float],
    guard: dict,
    slip: float,
    ts: pd.Timestamp,
) -> bool:
    action = rule["action"]
    equity_now = pf.equity(prices)
    turnover_left = equity_now * guard["daily_turnover_cap_pct"] / 100 - pf.traded_today_usd

    if action == "hold":
        return False
    if action == "buy":
        target = rule["target"]
        spend = pf.cash_usd * rule["size_pct"] / 100
        spend = min(spend, equity_now * guard["per_trade_cap_pct"] / 100, turnover_left, pf.cash_usd)
        if spend <= 1.0:
            return False
        price = prices[target]
        qty = spend * (1 - slip) / price
        pf.cash_usd -= spend
        pf.holdings[target] = pf.holdings.get(target, 0.0) + qty
        pf.traded_today_usd += spend
        pf.trades.append(_trade(ts, "buy", target, spend, qty, price))
        return True
    if action in ("sell", "rotate_to"):
        target = rule.get("target", guard["kill_switch_target"])
        return _rotate_all(pf, target, prices, slip, ts, reason=action)
    raise BacktestError(f"unknown action: {action}")


def _rotate_all(
    pf: _Portfolio,
    target: str,
    prices: dict[str, float],
    slip: float,
    ts: pd.Timestamp,
    reason: str,
) -> bool:
    moved = False
    for tok in list(pf.holdings):
        if tok == target:
            continue
        qty = pf.holdings.pop(tok)
        usd = qty * prices.get(tok, 1.0) * (1 - slip)
        if target in STABLES:
            pf.cash_usd += usd
        else:
            pf.holdings[target] = pf.holdings.get(target, 0.0) + usd * (1 - slip) / prices[target]
        pf.traded_today_usd += usd
        pf.trades.append(_trade(ts, reason, tok, usd, qty, prices.get(tok, 1.0)))
        moved = True
    return moved


def _trade(ts: pd.Timestamp, action: str, token: str, usd: float, qty: float, price: float) -> dict:
    return {
        "date": ts.date().isoformat(),
        "action": action,
        "token": token,
        "usd": round(usd, 2),
        "qty": round(qty, 8),
        "price": round(price, 6),
    }
