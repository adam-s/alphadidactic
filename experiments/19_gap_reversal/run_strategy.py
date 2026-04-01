# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""19 Gap Reversal — Buy gap-down stocks, sell gap-up stocks.

Hypothesis: stocks with the largest negative overnight gaps mean-revert
during the trading day. Cross-sectional: rank 153 symbols by gap size
at 09:35, enter top-N most negative gaps, exit at 15:30.

Gap = p0935[T] / p1530[T-1] - 1  (observable at 09:35)
Return = p1530[T] / p0935[T] - 1  (intraday, same-day settle)

Configs test:
  - top1_long: Buy single worst gap-down stock
  - top3_long: Buy 3 worst gap-down, equal weight
  - top5_long: Buy 5 worst gap-down, equal weight
  - top3_long_filtered: top3 but only if gap < -1% (filter noise)
  - top3_longshort: Long 3 worst gap-down + short 3 worst gap-up

No HMM, no regime gate, no EMA — pure cross-sectional mean-reversion.

Usage:
    python experiments/19_gap_reversal/run_strategy.py
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE, is_split
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay, Checkpoint,
    ResolutionMode, build_schedule, build_price_cache, load_price_cache,
)
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.metrics import sharpe
from shared.research_core import INITIAL_CAPITAL, get_symbols

OUT = HERE / "output"
SPLIT_THR = SPLIT_THRESHOLD
EXCLUDE = {"SPY", "QQQ", "VXX", "TQQQ"}

# Minimum gap magnitude to filter noise
MIN_GAP = 0.001  # 0.1% — smaller gaps are noise

CONFIGS = {
    # Reversal (buy gap-down)
    "top3_long":            {"top_n": 3, "min_gap": MIN_GAP, "long_short": False, "inverse": False, "exit_cp": "p1530", "gap_base": "p1530"},
    "top5_long":            {"top_n": 5, "min_gap": MIN_GAP, "long_short": False, "inverse": False, "exit_cp": "p1530", "gap_base": "p1530"},
    "top3_longshort":       {"top_n": 3, "min_gap": MIN_GAP, "long_short": True,  "inverse": False, "exit_cp": "p1530", "gap_base": "p1530"},
    # Inverse (buy gap-up, momentum continuation)
    "top3_inv":             {"top_n": 3, "min_gap": MIN_GAP, "long_short": False, "inverse": True,  "exit_cp": "p1530", "gap_base": "p1530"},
    "top5_inv":             {"top_n": 5, "min_gap": MIN_GAP, "long_short": False, "inverse": True,  "exit_cp": "p1530", "gap_base": "p1530"},
    # Timing variants
    "top3_inv_exit1030":    {"top_n": 3, "min_gap": MIN_GAP, "long_short": False, "inverse": True,  "exit_cp": "p1030", "gap_base": "p1530"},
    "top3_inv_exit1100":    {"top_n": 3, "min_gap": MIN_GAP, "long_short": False, "inverse": True,  "exit_cp": "p1100", "gap_base": "p1530"},
    "top3_inv_exit1200":    {"top_n": 3, "min_gap": MIN_GAP, "long_short": False, "inverse": True,  "exit_cp": "p1200", "gap_base": "p1530"},
    # p1600 as gap base (true close instead of 15:30)
    "top3_inv_close":       {"top_n": 3, "min_gap": MIN_GAP, "long_short": False, "inverse": True,  "exit_cp": "p1530", "gap_base": "p1600"},
}


def run_gap_reversal(tape, trading_days, config_name, cfg, *, min_gap_override=None,
                     inverse_override=None):
    """Run one gap reversal config. Returns dict with metrics.

    inverse=False: buy gap-DOWN stocks (mean-reversion hypothesis)
    inverse=True: buy gap-UP stocks (momentum continuation hypothesis)
    """
    top_n = cfg["top_n"]
    min_gap = min_gap_override if min_gap_override is not None else cfg["min_gap"]
    long_short = cfg["long_short"]
    inverse = inverse_override if inverse_override is not None else cfg.get("inverse", False)
    exit_cp = cfg.get("exit_cp", "p1530")
    gap_base = cfg.get("gap_base", "p1530")

    base_symbols = get_symbols()
    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    prev_gap_base = {}
    n_trades = 0
    n_wins = 0
    n_losses = 0

    for today in trading_days:
        t = tape.get(today)
        if t is None:
            continue
        p0935 = t.get("p0935", {})
        p_exit = t.get(exit_cp, {})
        p_gap = t.get(gap_base, {})

        # Compute gaps for all symbols (observable at 09:35)
        gaps = []
        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_gap_base.get(sym)
            op = p0935.get(sym)
            if not pc or not op or pc <= 0 or op <= 0:
                continue
            gap = op / pc - 1
            # Split filter on signal side
            if abs(gap) >= SPLIT_THR:
                continue
            gaps.append((gap, sym))

        gaps.sort()  # Most negative first

        if not inverse:
            # Standard: buy gap-DOWN stocks (mean-reversion)
            long_cands = [(g, s) for g, s in gaps if g < -min_gap][:top_n]
        else:
            # Inverse: buy gap-UP stocks (momentum continuation)
            long_cands = [(g, s) for g, s in reversed(gaps) if g > min_gap][:top_n]

        # Select short candidates (biggest gap-up) — only for longshort, non-inverse
        short_cands = []
        if long_short and not inverse:
            short_cands = [(g, s) for g, s in reversed(gaps) if g > min_gap][:top_n]

        # Compute intraday returns (entry at p0935, exit at exit_cp)
        long_rets = []
        for gap_val, sym in long_cands:
            ep = p0935.get(sym)
            xp = p_exit.get(sym)
            if ep and xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                if abs(rr) < SPLIT_THR:
                    long_rets.append(rr - 2 * TC)
                    n_trades += 1
                    if rr - 2 * TC > 0:
                        n_wins += 1
                    elif rr - 2 * TC < 0:
                        n_losses += 1

        short_rets = []
        for gap_val, sym in short_cands:
            ep = p0935.get(sym)
            xp = p_exit.get(sym)
            if ep and xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                if abs(rr) < SPLIT_THR:
                    # Short: profit when price drops
                    short_rets.append(-(rr) - 2 * TC)
                    n_trades += 1
                    if -(rr) - 2 * TC > 0:
                        n_wins += 1
                    elif -(rr) - 2 * TC < 0:
                        n_losses += 1

        # Combine (equal weight across all positions)
        all_rets = long_rets + short_rets
        day_ret = np.mean(all_rets) if all_rets else 0.0

        equity *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

        prev_gap_base = {s: p for s, p in p_gap.items() if p is not None}

    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    tts = pd.Timestamp(TRAIN_END)

    return {
        "train_sh": round(float(sharpe(dr[dt <= tts])), 3),
        "test_sh": round(float(sharpe(dr[dt > tts])), 3),
        "daily_rets": daily_rets, "dates": dates,
        "n_trades": n_trades, "n_wins": n_wins, "n_losses": n_losses,
    }


def main():
    base_symbols = get_symbols()
    all_symbols = sorted(set(base_symbols + ["SPY", "VXX"]))

    # All exit times needed by configs
    all_times = {clock_time(9, 35), clock_time(10, 30), clock_time(11, 0),
                 clock_time(12, 0), clock_time(15, 30), clock_time(16, 0)}
    checkpoints = []
    for t in sorted(all_times):
        name = f"p{t.hour:02d}{t.minute:02d}"
        grace = 390 if t == clock_time(16, 0) else 5  # p1600 wide grace for SPY B&H
        checkpoints.append(
            Checkpoint(name=name, target_time_et=t, mode=ResolutionMode.AT_OR_BEFORE,
                       grace_minutes_before=grace, grace_minutes_after=0,
                       required=False, trading_day_offset=0))
    schedule = build_schedule("exp19_gap_rev", checkpoints)

    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(all_symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    conn.close()

    # Build tape with all checkpoints
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        day_prices = {}
        for cp in checkpoints:
            prices = {}
            for sym in all_symbols:
                v = day_data.get(sym, {}).get(cp.name)
                if v is not None:
                    prices[sym] = v
            day_prices[cp.name] = prices
        tape[td] = day_prices

    # SPY B&H
    spy_day_rets = {}
    prev_spy = None
    for td in trading_days:
        spy_p = price_cache.get(td, {}).get("SPY", {}).get("p1600")
        if spy_p and prev_spy and prev_spy > 0:
            r = spy_p / prev_spy - 1
            if abs(r) < SPLIT_THR:
                spy_day_rets[td] = r
        if spy_p:
            prev_spy = spy_p

    # Run all configs
    results = {}
    config_series = {}
    print(f"\n{'='*100}", file=sys.stderr)
    print(f"  19 — GAP REVERSAL (cross-sectional mean-reversion)", file=sys.stderr)
    print(f"{'='*100}", file=sys.stderr)
    print(f"  {'Config':<25s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'N':>5s}", file=sys.stderr)
    print(f"  {'-'*25}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*5}", file=sys.stderr)

    for config_name in tqdm(list(CONFIGS.keys()), desc="Configs", file=sys.stderr):
        cfg = CONFIGS[config_name]
        m = run_gap_reversal(tape, trading_days, config_name, cfg)
        config_series[config_name] = m

        dr = np.array(m["daily_rets"])
        cum = np.cumprod(1 + dr)
        pk = np.maximum.accumulate(cum)
        mdd = float(abs((cum / pk - 1).min()) * 100)
        full_ret = float((np.prod(1 + dr) - 1) * 100)
        nz = [r for r in dr if r != 0]
        wr = float(np.mean([r > 0 for r in nz]) * 100) if nz else 0

        results[config_name] = {
            "train_sh": m["train_sh"], "test_sh": m["test_sh"],
            "ret": round(full_ret, 1), "dd": round(mdd, 2), "wr": round(wr, 1),
            "n": m["n_trades"],
        }
        print(f"  {config_name:<25s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.02f}  {full_ret:>+7.0f}%  {mdd:>5.1f}%  {wr:>4.0f}%  {m['n_trades']:>5d}", file=sys.stderr)

    print(f"{'='*100}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for name, m in config_series.items():
        dr = np.array(m["daily_rets"])
        pd.DataFrame({
            "date": m["dates"], "day_ret": m["daily_rets"],
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{name}.parquet", index=False)

    # Bookkeeping for primary (top3_inv — best test Sharpe, momentum continuation)
    primary = "top3_inv"
    pm = config_series[primary]
    metrics = compute_experiment_metrics(pm["daily_rets"], pm["dates"], pm["n_trades"], pm["n_wins"], pm["n_losses"])
    print_results("19 GAP REVERSAL (top3_long)", metrics)
    save_results(OUT, primary, pm["daily_rets"], pm["dates"], metrics, [])
    plot_pnl(OUT, "Gap Reversal (top3_long)", pm["daily_rets"], pm["dates"],
             trading_days, spy_day_rets, metrics, color="#8b5cf6")

    # Statistical robustness
    from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
    p_dr = np.array(pm["daily_rets"])
    spy_all = np.array([spy_day_rets.get(d, 0.0) for d in pm["dates"]])
    stat_results = {
        "permutation": permutation_test(p_dr, spy_all, tc_per_active_day=2 * TC),
        "bootstrap": bootstrap_sharpe_ci(p_dr),
        "concentration": concentration_ratio(p_dr),
    }
    (OUT / "stat_tests.json").write_text(json.dumps(stat_results, indent=2, default=str))
    for sname, res in stat_results.items():
        status = "PASS" if res.get("pass") else "FAIL" if res.get("pass") is False else "N/A"
        print(f"  {sname}: {status} — {res.get('interpretation', '')}", file=sys.stderr)


if __name__ == "__main__":
    main()
