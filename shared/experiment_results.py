"""Experiment results bookkeeping — metrics, output, and plotting.

Every experiment computes the same metrics, writes the same outputs, and
produces the same P&L chart. This module handles that so experiments focus
on signal logic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import matplotlib.gridspec as gridspec

from shared.config import TRAIN_END
from shared.metrics import sharpe
from shared.research_core import INITIAL_CAPITAL


def compute_experiment_metrics(
    daily_rets: list[float],
    dates: list,
    n_trades: int = 0,
    n_wins: int = 0,
    n_losses: int = 0,
) -> dict:
    """Compute train/test Sharpe, return, drawdown, win rate."""
    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    train_mask = dt <= pd.Timestamp(TRAIN_END)

    cum = np.cumprod(1 + dr)
    peak = np.maximum.accumulate(cum)

    return {
        "train_sharpe": round(float(sharpe(dr[train_mask])), 3),
        "test_sharpe": round(float(sharpe(dr[~train_mask])), 3),
        "total_return_pct": round(float((np.prod(1 + dr) - 1) * 100), 1),
        "max_drawdown_pct": round(float(abs((cum / peak - 1).min()) * 100), 2),
        "win_rate_pct": round(
            float(np.mean([r > 0 for r in dr if r != 0]) * 100)
            if any(r != 0 for r in dr) else 0, 1
        ),
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_losses,
    }


def print_results(title: str, metrics: dict):
    """Print results summary to stderr."""
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  {title}", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)
    print(f"  Train Sharpe: {metrics['train_sharpe']:+.3f}", file=sys.stderr)
    print(f"  Test Sharpe:  {metrics['test_sharpe']:+.3f}", file=sys.stderr)
    print(f"  Total Return: {metrics['total_return_pct']:+.1f}%", file=sys.stderr)
    print(f"  Max Drawdown: {metrics['max_drawdown_pct']:.1f}%", file=sys.stderr)
    print(f"  Win Rate:     {metrics['win_rate_pct']:.0f}%", file=sys.stderr)
    print(f"  Trades:       {metrics['n_trades']} ({metrics['n_wins']}W / {metrics['n_losses']}L)",
          file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)


def save_results(
    out_dir: Path,
    leg_name: str,
    daily_rets: list[float],
    dates: list,
    metrics: dict,
    data_gaps: list[dict],
    trade_log: list[dict] | None = None,
):
    """Write results JSON, parquet, data gaps, and optional trade log."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dr = np.array(daily_rets)

    # Results JSON
    results = {"leg": leg_name, **metrics}
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Results parquet
    pd.DataFrame({
        "date": dates,
        "day_ret": daily_rets,
        "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
    }).to_parquet(out_dir / "results.parquet", index=False)

    # Data gaps
    (out_dir / "data_gaps.json").write_text(json.dumps(data_gaps, indent=2))
    if data_gaps:
        print(f"  WARNING: {len(data_gaps)} data gaps — see {out_dir / 'data_gaps.json'}",
              file=sys.stderr)

    # Trade log
    if trade_log:
        (out_dir / "trade_log.json").write_text(json.dumps(trade_log, indent=2, default=str))


def plot_pnl(
    out_dir: Path,
    title: str,
    daily_rets: list[float],
    dates: list,
    trading_days: list,
    spy_day_rets: dict,
    metrics: dict,
    color: str = "#2563eb",
):
    """Generate P&L chart with SPY buy-and-hold benchmark."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dr = np.array(daily_rets)

    # Strategy equity
    eq_arr = np.insert(INITIAL_CAPITAL * np.cumprod(1 + dr), 0, INITIAL_CAPITAL)
    strat_dates = pd.to_datetime([dates[0]] + list(dates))

    # SPY benchmark
    spy_eq = [INITIAL_CAPITAL]
    for d in trading_days:
        spy_eq.append(spy_eq[-1] * (1 + spy_day_rets.get(d, 0.0)))
    spy_arr = np.array(spy_eq)
    spy_dates = pd.to_datetime([trading_days[0]] + list(trading_days))

    def _rolling_dd(v):
        v = np.asarray(v, float)
        p = np.maximum.accumulate(v)
        return (v - p) / p * 100

    spy_color = "#4a5568"
    tr_sh = metrics["train_sharpe"]
    te_sh = metrics["test_sharpe"]
    total_ret = metrics["total_return_pct"]
    max_dd = metrics["max_drawdown_pct"]
    win_rate = metrics["win_rate_pct"]

    plt.rcParams.update({
        "figure.dpi": 150, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.3, "font.size": 9,
    })
    fig = plt.figure(figsize=(14, 8))
    gs = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.15)

    ax = fig.add_subplot(gs[0])
    spy_ret_pct = (spy_arr[-1] / spy_arr[0] - 1) * 100
    ax.plot(spy_dates, spy_arr, color=spy_color, linewidth=1.2, alpha=0.7,
            label=f"SPY B&H (+{spy_ret_pct:.0f}%)")
    ax.plot(strat_dates, eq_arr, color=color, linewidth=1.5,
            label=f"{title} (+{total_ret:.0f}%, Tr {tr_sh:.2f}/Te {te_sh:.2f})")

    train_end_num = float(mdates.date2num(pd.Timestamp(TRAIN_END).to_pydatetime()))
    ax.axvline(train_end_num, color="black", linewidth=1.5, linestyle="--")
    ax.text(train_end_num, eq_arr.max() * 0.85, "  Test ->", fontsize=10, fontweight="bold")
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.set_title(
        f"{title} | Tr {tr_sh:.2f} | Te {te_sh:.2f} | "
        f"+{total_ret:.0f}% | DD {max_dd:.1f}% | WR {win_rate:.0f}%",
        fontweight="bold", fontsize=10,
    )
    ax.legend(fontsize=9, loc="upper left")
    ax.set_ylabel("Portfolio ($)")

    ax2 = fig.add_subplot(gs[1], sharex=ax)
    ax2.fill_between(strat_dates, _rolling_dd(eq_arr), 0, alpha=0.25, color=color)
    ax2.fill_between(spy_dates, _rolling_dd(spy_arr), 0, alpha=0.15, color=spy_color)
    ax2.axvline(train_end_num, color="black", linewidth=1.5, linestyle="--")
    ax2.set_ylabel("DD (%)")
    ax2.set_xlabel("Date")
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    plt.savefig(out_dir / "pnl_chart.png", bbox_inches="tight")
    plt.close()
    print(f"  Chart: {out_dir / 'pnl_chart.png'}", file=sys.stderr)
