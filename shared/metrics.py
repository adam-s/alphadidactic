"""
metrics.py — Sharpe, compute_metrics.

Flat/missing days count as 0 return. No cherry-picking active days.
"""

import numpy as np


def sharpe(daily_returns, annualize: bool = True) -> float:
    """Annualized Sharpe ratio from daily returns array.

    Flat days (no signal) must be included as 0.0 in the array.
    """
    arr = np.asarray(daily_returns, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2 or np.std(arr) == 0:
        return 0.0
    s = np.mean(arr) / np.std(arr)
    if annualize:
        s *= np.sqrt(252)
    return float(s)


def compute_metrics(equity_curve: list[float] | np.ndarray) -> dict:
    """Compute standard backtest metrics from an equity curve.

    Args:
        equity_curve: list of portfolio values (starting with initial capital)

    Returns:
        dict with: sharpe, total_return_pct, max_drawdown_pct, win_rate_pct,
                   calmar, n_days, n_trades
    """
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) < 2:
        return {"sharpe": 0, "total_return_pct": 0, "max_drawdown_pct": 0,
                "win_rate_pct": 0, "calmar": 0, "n_days": 0, "n_trades": 0}

    daily_ret = np.diff(eq) / eq[:-1]
    daily_ret = daily_ret[~np.isnan(daily_ret)]

    # Max drawdown
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = float(abs(dd.min())) if len(dd) > 0 else 0

    total_ret = (eq[-1] / eq[0] - 1) * 100
    s = sharpe(daily_ret)
    calmar = (total_ret / 100) / max_dd if max_dd > 0 else 0

    # Win rate (non-zero days only)
    nonzero = daily_ret[daily_ret != 0]
    win_rate = float(np.sum(nonzero > 0) / len(nonzero) * 100) if len(nonzero) > 0 else 0

    return {
        "sharpe": round(s, 3),
        "total_return_pct": round(total_ret, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "win_rate_pct": round(win_rate, 1),
        "calmar": round(calmar, 3),
        "n_days": len(daily_ret),
        "n_trades": int(np.sum(nonzero != 0)),
    }
