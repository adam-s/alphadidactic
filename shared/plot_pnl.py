"""
plot_pnl.py — Standard 2-panel P/L chart for any experiment.

Generates a publication-quality equity curve + drawdown chart from
results.parquet. Called by run_strategy.py after computing results.

Usage:
    from shared.plot_pnl import save_pnl_chart
    save_pnl_chart(results_df, output_dir, title="My Experiment")

Input: DataFrame with columns: date, equity, day_ret, is_train
Optional: benchmark equity array for overlay (e.g., buy-and-hold)
Output: PNG saved to output_dir/pnl_chart.png
"""

import os
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Lazy import matplotlib — only when chart is actually generated
_plt = None
_mdates = None
_mticker = None
_gridspec = None


def _ensure_matplotlib():
    global _plt, _mdates, _mticker, _gridspec
    if _plt is None:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend for headless servers
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.ticker as mticker
        import matplotlib.gridspec as gridspec
        _plt = plt
        _mdates = mdates
        _mticker = mticker
        _gridspec = gridspec


def _rolling_dd(vals: np.ndarray) -> np.ndarray:
    """Rolling drawdown from peak, in percent."""
    vals = np.asarray(vals, dtype=float)
    peak = np.maximum.accumulate(vals)
    return (vals - peak) / peak * 100


def save_pnl_chart(
    results_df: pd.DataFrame,
    output_dir: str,
    title: str = "Strategy P/L",
    benchmark_equity: np.ndarray | None = None,
    benchmark_label: str = "Benchmark",
    strategy_color: str = "#2563eb",
    benchmark_color: str = "#9ca3af",
    filename: str = "pnl_chart.png",
) -> str:
    """
    Generate a 2-panel P/L chart: equity curve (log) + drawdown.

    Args:
        results_df: DataFrame with columns [date, equity, day_ret, is_train].
        output_dir: Directory to save the PNG.
        title: Chart title (experiment name + key metrics).
        benchmark_equity: Optional array of benchmark equity values (same length as results).
        benchmark_label: Label for the benchmark line.
        strategy_color: Hex color for strategy line.
        benchmark_color: Hex color for benchmark line.
        filename: Output filename.

    Returns:
        Path to saved PNG file.
    """
    _ensure_matplotlib()
    plt, mdates, mticker, gridspec = _plt, _mdates, _mticker, _gridspec

    df = results_df.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(df["date"])
    equity = df["equity"].values

    # Find train/test boundary
    train_mask = df["is_train"].astype(bool)
    if train_mask.any() and (~train_mask).any():
        test_start_idx = (~train_mask).idxmax()
        test_start_date = dates.iloc[test_start_idx]
    else:
        test_start_date = None

    # Compute metrics for title
    from shared.metrics import sharpe, compute_metrics

    train_rets = df.loc[train_mask, "day_ret"].values
    test_rets = df.loc[~train_mask, "day_ret"].values
    full_metrics = compute_metrics(equity)

    train_sharpe = sharpe(train_rets) if len(train_rets) > 1 else 0
    test_sharpe = sharpe(test_rets) if len(test_rets) > 1 else 0

    # Build title with metrics
    auto_title = (
        f"{title}\n"
        f"Return: {full_metrics['total_return_pct']:+.1f}%  |  "
        f"Train Sh: {train_sharpe:.2f}  |  Test Sh: {test_sharpe:.2f}  |  "
        f"Max DD: {full_metrics['max_drawdown_pct']:.1f}%  |  "
        f"Win: {full_metrics['win_rate_pct']:.0f}%  |  "
        f"N: {full_metrics['n_trades']}"
    )

    # Style
    plt.rcParams.update({
        "figure.dpi": 150,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "font.size": 9,
    })

    fig = plt.figure(figsize=(14, 8))
    gs = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.15)

    # ── Panel 1: Equity curve ────────────────────────────────────────────
    ax_eq = fig.add_subplot(gs[0])

    # Benchmark overlay (if provided)
    if benchmark_equity is not None:
        bh_eq = np.asarray(benchmark_equity, dtype=float)
        bh_ret = (bh_eq[-1] / bh_eq[0] - 1) * 100
        ax_eq.plot(dates, bh_eq, color=benchmark_color, linewidth=1.2, alpha=0.7,
                   label=f"{benchmark_label} ({bh_ret:+.0f}%)")

    # Strategy
    strat_ret = (equity[-1] / equity[0] - 1) * 100
    ax_eq.plot(dates, equity, color=strategy_color, linewidth=1.5,
               label=f"Strategy ({strat_ret:+.0f}%)")

    # Train/test split line
    if test_start_date is not None:
        tn = float(mdates.date2num(test_start_date.to_pydatetime()))
        ax_eq.axvline(tn, color="black", linewidth=1.2, linestyle="--")
        ax_eq.text(tn, equity.max() * 0.85, "  Test →", fontsize=9, fontweight="bold")

    ax_eq.set_yscale("log")
    ax_eq.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax_eq.set_title(auto_title, fontweight="bold", fontsize=10)
    ax_eq.legend(fontsize=9, loc="upper left")
    ax_eq.set_ylabel("Equity ($)")
    ax_eq.tick_params(axis="x", labelbottom=False)

    # ── Panel 2: Drawdown ────────────────────────────────────────────────
    ax_dd = fig.add_subplot(gs[1], sharex=ax_eq)

    if benchmark_equity is not None:
        ax_dd.fill_between(dates, _rolling_dd(bh_eq), 0, alpha=0.15, color=benchmark_color)

    ax_dd.fill_between(dates, _rolling_dd(equity), 0, alpha=0.25, color=strategy_color)
    ax_dd.plot(dates, _rolling_dd(equity), color=strategy_color, linewidth=0.8)

    if test_start_date is not None:
        ax_dd.axvline(tn, color="black", linewidth=1.2, linestyle="--")

    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.set_xlabel("Date")
    ax_dd.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax_dd.xaxis.set_major_locator(mdates.YearLocator())
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # Save
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, filename)
    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    logger.info(f"P/L chart saved → {out_path}")
    return out_path
