"""
Strategy performance metrics.

Extracted from spy_flow_spreads/shared/data.py — only the pure math functions.
Does NOT include load_daily_prices (which had the DISTINCT ON bug).
"""
import numpy as np
import pandas as pd

INITIAL_CAPITAL = 10_000


def calc_metrics(daily_values, initial_capital=INITIAL_CAPITAL,
                 period_start=None, period_end=None):
    """Calculate strategy metrics from daily portfolio values.

    Args:
        daily_values: DataFrame with columns [date, value]
        initial_capital: starting capital for return calculations
        period_start/end: if provided, fills business days with 0 returns
            for proper Sharpe calculation over a fixed window

    Returns dict: sharpe, total_return_pct, cagr_pct, max_dd_pct, calmar
    """
    if len(daily_values) < 2:
        return {"sharpe": 0, "cagr_pct": 0, "max_dd_pct": 0,
                "total_return_pct": 0, "calmar": 0}

    vals = daily_values.copy()
    vals["date"] = pd.to_datetime(vals["date"])
    vals = vals.sort_values("date").reset_index(drop=True)

    vals["return"] = vals["value"].pct_change()

    rets = vals["return"].dropna()
    if period_start and period_end:
        rets_indexed = rets.copy()
        rets_indexed.index = vals.loc[rets.index, "date"]
        bdays = pd.bdate_range(period_start, period_end)
        rets = rets_indexed.reindex(bdays, fill_value=0.0)

    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0

    total_return_pct = (vals["value"].iloc[-1] / initial_capital - 1) * 100

    n_years = (vals["date"].iloc[-1] - vals["date"].iloc[0]).days / 365.25
    if n_years > 0 and vals["value"].iloc[-1] > 0:
        cagr_pct = ((vals["value"].iloc[-1] / initial_capital) ** (1 / n_years) - 1) * 100
    else:
        cagr_pct = 0.0

    peak = vals["value"].cummax()
    drawdown_pct = (peak - vals["value"]) / peak * 100
    max_dd_pct = float(drawdown_pct.max())

    calmar = abs(cagr_pct / max_dd_pct) if max_dd_pct > 0 else 0.0

    return {
        "sharpe": sharpe,
        "total_return_pct": float(total_return_pct),
        "cagr_pct": float(cagr_pct),
        "max_dd_pct": max_dd_pct,
        "calmar": calmar,
    }


def calc_spread_metrics(trades, initial_capital=INITIAL_CAPITAL,
                        period_start=None, period_end=None):
    """Calculate spread-specific metrics from trade list."""
    if not trades:
        return {"win_rate": 0, "profit_factor": 0, "avg_pnl": 0, "num_trades": 0}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / len(pnls) if pnls else 0
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_pnl": float(np.mean(pnls)),
        "num_trades": len(pnls),
    }
