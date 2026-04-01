# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""18 Dual Intraday Checkpoint — Morning + afternoon as separate compounding legs.

Instead of one intraday leg (10:30→16:00), split into TWO:
  Morning: 10:30 → split_time (gold trend first half)
  Afternoon: split_time → 16:00 (gold trend second half)

Each half compounds independently: equity *= (1 + morning) * (1 + afternoon).
Signal (NUGT EMA > 0) computed once at 09:35 — causal.

Configs test different split times.

Usage:
    python experiments/18_dual_intraday/run_strategy.py
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
from shared.indicators import OnlineEMA
from shared.metrics import sharpe
from shared.research_core import INITIAL_CAPITAL

OUT = HERE / "output"
SPLIT_THR = SPLIT_THRESHOLD
EMA_SPAN = 34

CONFIGS = {
    "single_1030_1600":     {"split": None,                "entry": clock_time(10, 30), "exit": clock_time(16, 0)},
    "dual_1030_1230_1600":  {"split": clock_time(12, 30),  "entry": clock_time(10, 30), "exit": clock_time(16, 0)},
    "dual_1030_1300_1600":  {"split": clock_time(13, 0),   "entry": clock_time(10, 30), "exit": clock_time(16, 0)},
    "dual_1030_1330_1600":  {"split": clock_time(13, 30),  "entry": clock_time(10, 30), "exit": clock_time(16, 0)},
    "dual_1030_1400_1600":  {"split": clock_time(14, 0),   "entry": clock_time(10, 30), "exit": clock_time(16, 0)},
}


def run_dual(tape, trading_days, config_name, cfg, *, split_override=None):
    """Run one config. Returns dict with metrics + daily_rets + dates.

    split_override: clock_time to override cfg's split time (for Optuna).
    """
    ema = OnlineEMA(EMA_SPAN)
    prev_close = {}

    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    n_trades = 0
    n_wins = 0
    n_losses = 0

    split_time = split_override if split_override is not None else cfg["split"]

    for today in trading_days:
        td = tape.get(today)
        if td is None:
            continue
        p0935 = td.get("p0935", {})

        # Update EMA with NUGT overnight return
        nugt_pc = prev_close.get("NUGT")
        nugt_op = p0935.get("NUGT")
        if nugt_pc and nugt_op and nugt_pc > 0 and nugt_op > 0:
            r = nugt_op / nugt_pc - 1
            if abs(r) < SPLIT_THR:
                ema.update(r)

        ev = ema.get()
        go = ev is not None and ev > 0

        entry_cp = f"p{cfg['entry'].hour:02d}{cfg['entry'].minute:02d}"
        exit_cp = f"p{cfg['exit'].hour:02d}{cfg['exit'].minute:02d}"
        p_entry = td.get(entry_cp, {})
        p_exit = td.get(exit_cp, {})

        day_ret = 0.0

        if go:
            if split_time is None:
                # Single leg
                ep = p_entry.get("NUGT")
                xp = p_exit.get("NUGT")
                if ep and xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    if abs(rr) < SPLIT_THR:
                        day_ret = rr - 2 * TC
                        n_trades += 1
                        if day_ret > 0:
                            n_wins += 1
                        elif day_ret < 0:
                            n_losses += 1
            else:
                # Dual leg
                split_cp = f"p{split_time.hour:02d}{split_time.minute:02d}"
                p_split = td.get(split_cp, {})

                ep = p_entry.get("NUGT")
                sp_price = p_split.get("NUGT")
                xp = p_exit.get("NUGT")

                morning_ret = 0.0
                afternoon_ret = 0.0
                if ep and sp_price and ep > 0 and sp_price > 0:
                    rr = sp_price / ep - 1
                    if abs(rr) < SPLIT_THR:
                        morning_ret = rr - 2 * TC
                        n_trades += 1
                        if morning_ret > 0:
                            n_wins += 1
                        elif morning_ret < 0:
                            n_losses += 1

                if sp_price and xp and sp_price > 0 and xp > 0:
                    rr = xp / sp_price - 1
                    if abs(rr) < SPLIT_THR:
                        afternoon_ret = rr - 2 * TC
                        n_trades += 1
                        if afternoon_ret > 0:
                            n_wins += 1
                        elif afternoon_ret < 0:
                            n_losses += 1

                # Multiplicative compounding
                equity_before = equity
                equity *= (1 + morning_ret)
                equity *= (1 + afternoon_ret)
                day_ret = equity / equity_before - 1 if equity_before > 0 else 0.0
                daily_rets.append(day_ret)
                dates.append(today)

                # Store close prices (same R6 pattern as reference — last checkpoint overwrites)
                prev_close = {}
                for cp_name in sorted(td.keys()):
                    for sym, p in td.get(cp_name, {}).items():
                        prev_close[sym] = p
                continue

        equity *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

        # Store close prices
        prev_close = {}
        for cp_name in sorted(td.keys()):
            for sym, p in td.get(cp_name, {}).items():
                prev_close[sym] = p

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
    symbols = ["GLD", "GDX", "NUGT", "SPY", "VXX"]

    # Build checkpoints for all split times
    all_times = {clock_time(9, 35), clock_time(10, 30), clock_time(16, 0)}
    for cfg in CONFIGS.values():
        if cfg["split"]:
            all_times.add(cfg["split"])

    checkpoints = []
    for t in sorted(all_times):
        name = f"p{t.hour:02d}{t.minute:02d}"
        checkpoints.append(
            Checkpoint(name=name, target_time_et=t, mode=ResolutionMode.AT_OR_BEFORE,
                       grace_minutes_before=5, grace_minutes_after=0,
                       required=False, trading_day_offset=0))

    schedule = build_schedule("exp18_dual", checkpoints)
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    # Reference used date.today() — converted to END_DATE for reproducibility.
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    conn.close()

    # Build tape from price cache
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        day_prices = {}
        for cp in checkpoints:
            prices = {}
            for sym in symbols:
                v = day_data.get(sym, {}).get(cp.name)
                if v is not None:
                    prices[sym] = v
            day_prices[cp.name] = prices
        tape[td] = day_prices

    # SPY B&H benchmark (open-to-open via p0935, matching reference R6 pattern)
    spy_day_rets = {}
    prev_spy = None
    for td in trading_days:
        so = tape.get(td, {}).get("p0935", {}).get("SPY")
        if so and prev_spy and prev_spy > 0:
            r = so / prev_spy - 1
            if abs(r) < SPLIT_THR:
                spy_day_rets[td] = r
        # prev_close uses last checkpoint (R6 pattern — matches reference)
        day_data = price_cache.get(td, {})
        for cp in checkpoints:
            v = day_data.get("SPY", {}).get(cp.name)
            if v is not None:
                prev_spy = v

    # Run all configs
    results = {}
    config_series = {}
    print(f"\n{'='*100}", file=sys.stderr)
    print(f"  18 — DUAL INTRADAY CHECKPOINT (morning + afternoon)", file=sys.stderr)
    print(f"{'='*100}", file=sys.stderr)
    print(f"  {'Config':<30s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'N':>5s}", file=sys.stderr)
    print(f"  {'-'*30}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*5}", file=sys.stderr)

    for config_name in tqdm(list(CONFIGS.keys()), desc="Configs", file=sys.stderr):
        cfg = CONFIGS[config_name]
        m = run_dual(tape, trading_days, config_name, cfg)
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
        print(f"  {config_name:<30s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.02f}  {full_ret:>+7.0f}%  {mdd:>5.1f}%  {wr:>4.0f}%  {m['n_trades']:>5d}", file=sys.stderr)

    print(f"{'='*100}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for name, m in config_series.items():
        dr = np.array(m["daily_rets"])
        pd.DataFrame({
            "date": m["dates"], "day_ret": m["daily_rets"],
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{name}.parquet", index=False)

    # Bookkeeping for primary (single_1030_1600 — baseline)
    primary = "single_1030_1600"
    pm = config_series[primary]
    metrics = compute_experiment_metrics(pm["daily_rets"], pm["dates"], pm["n_trades"], pm["n_wins"], pm["n_losses"])
    print_results("18 DUAL INTRADAY (single_1030_1600)", metrics)
    save_results(OUT, primary, pm["daily_rets"], pm["dates"], metrics, [])
    plot_pnl(OUT, "Dual Intraday (single baseline)", pm["daily_rets"], pm["dates"],
             trading_days, spy_day_rets, metrics, color="#f59e0b")

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
