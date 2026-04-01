# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""20 Split Signal — Trade after inverse ETF reverse splits.

Finding: Inverse ETF reverse splits mark volatility PEAKS, not precursors.
Gold rises and VXX declines in the 20-40 days following splits.

Signal: Detect reverse split (price jump > 200%) on any inverse/vol ETF.
        Observable at 09:35 on split date (open price shows the jump).
Entry:  Next trading day at 09:35.
Exit:   N trading days later at 15:30.

Configs:
  - gld_20d: Buy GLD, hold 20 days (t=+2.10 in analysis)
  - gld_40d: Buy GLD, hold 40 days
  - short_vxx_20d: Short VXX, hold 20 days
  - short_vxx_40d: Short VXX, hold 40 days (t=+2.08 in analysis)
  - spy_20d: Buy SPY, hold 20 days (82% win rate)

Usage:
    python experiments/20_split_signal/run_strategy.py
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
from shared.research_core import INITIAL_CAPITAL

OUT = HERE / "output"
SPLIT_THR = SPLIT_THRESHOLD

# Inverse/vol ETFs to monitor for reverse splits
INVERSE_ETFS = ["VXX", "SQQQ", "SPXS", "SH", "SDS", "SOXS", "UVXY", "TZA", "UNG"]

# Minimum price jump to qualify as reverse split (not volatility)
REVERSE_SPLIT_MIN = 2.0  # 200% = ~3:1 ratio

CONFIGS = {
    "gld_20d":        {"instrument": "GLD", "hold_days": 20, "direction": "long"},
    "gld_40d":        {"instrument": "GLD", "hold_days": 40, "direction": "long"},
    "short_vxx_20d":  {"instrument": "VXX", "hold_days": 20, "direction": "short"},
    "short_vxx_40d":  {"instrument": "VXX", "hold_days": 40, "direction": "short"},
    "spy_20d":        {"instrument": "SPY", "hold_days": 20, "direction": "long"},
}


def detect_reverse_splits(price_cache, trading_days):
    """Detect reverse splits from price discontinuities on inverse/vol ETFs."""
    splits = []
    for sym in INVERSE_ETFS:
        prev_p = None
        for td in trading_days:
            p = price_cache.get(td, {}).get(sym, {}).get("p1600")
            if p and prev_p and prev_p > 0:
                r = p / prev_p - 1
                if r > REVERSE_SPLIT_MIN:
                    splits.append({"date": td, "symbol": sym, "ratio": round(p / prev_p)})
            if p:
                prev_p = p
    return sorted(splits, key=lambda x: x["date"])


def run_split_strategy(tape, trading_days, splits, config_name, cfg):
    """Run one split-triggered strategy config."""
    instrument = cfg["instrument"]
    hold_days = cfg["hold_days"]
    direction = cfg["direction"]  # "long" or "short"

    # Deduplicate split dates
    split_dates = sorted(set(s["date"] for s in splits))
    td_idx = {td: i for i, td in enumerate(trading_days)}

    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    n_trades = 0
    n_wins = 0
    n_losses = 0
    data_gaps = []

    # Track active position
    holding_until_idx = -1  # index in trading_days when position exits
    entry_price = None
    pending_split = False  # Signal detected, enter NEXT day

    for i, today in enumerate(trading_days):
        day_data = tape.get(today, {})
        p0935 = day_data.get("p0935", {})
        p1530 = day_data.get("p1530", {})

        day_ret = 0.0

        # Execute pending entry (signal was yesterday, enter today)
        if pending_split and entry_price is None:
            inst_entry = p0935.get(instrument)
            if inst_entry and inst_entry > 0:
                entry_price = inst_entry
                holding_until_idx = i + hold_days
                day_ret -= TC  # Entry TC applied on entry day
            pending_split = False

        # Compute daily return if holding
        if entry_price is not None:
            inst_today = p1530.get(instrument)
            inst_prev = None
            if i > 0:
                prev_day = tape.get(trading_days[i - 1], {})
                inst_prev = prev_day.get("p1530", {}).get(instrument)
            # First holding day: return is p1530/p0935 (entry to close)
            if inst_prev is None:
                inst_prev = entry_price  # Use entry price as "prev"

            if inst_today and inst_prev and inst_prev > 0 and inst_today > 0:
                raw_ret = inst_today / inst_prev - 1
                if abs(raw_ret) < SPLIT_THR:
                    if direction == "long":
                        day_ret += raw_ret
                    else:
                        day_ret += -raw_ret

            # Check if exit day
            if i >= holding_until_idx:
                day_ret -= TC  # Exit TC applied on exit day
                exit_price = p1530.get(instrument)
                if exit_price and entry_price > 0 and exit_price > 0:
                    total_ret = exit_price / entry_price - 1
                    if direction == "short":
                        total_ret = -total_ret
                    total_ret -= 2 * TC
                    n_trades += 1
                    if total_ret > 0:
                        n_wins += 1
                    elif total_ret < 0:
                        n_losses += 1
                else:
                    data_gaps.append({"date": str(today), "symbol": instrument,
                        "target": "15:30", "resolution": "missing_exit"})
                entry_price = None
                holding_until_idx = -1

        # Detect split event (signal today, enter TOMORROW)
        if entry_price is None and not pending_split:
            today_splits = [s for s in splits if s["date"] == today]
            if today_splits:
                pending_split = True

        equity *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    tts = pd.Timestamp(TRAIN_END)

    return {
        "train_sh": round(float(sharpe(dr[dt <= tts])), 3),
        "test_sh": round(float(sharpe(dr[dt > tts])), 3),
        "daily_rets": daily_rets, "dates": dates,
        "n_trades": n_trades, "n_wins": n_wins, "n_losses": n_losses,
        "data_gaps": data_gaps,
    }


def main():
    all_symbols = sorted(set(INVERSE_ETFS + ["SPY", "VXX", "GLD"]))

    schedule = build_schedule("exp20_split", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])

    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(all_symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    conn.close()

    # Build tape
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        p0935 = {}
        p1530 = {}
        for sym, prices in day_data.items():
            v = prices.get("p0935")
            if v is not None:
                p0935[sym] = v
            v = prices.get("p1530")
            if v is not None:
                p1530[sym] = v
        tape[td] = {"p0935": p0935, "p1530": p1530}

    # Detect splits
    splits = detect_reverse_splits(price_cache, trading_days)
    print(f"\n  Detected {len(splits)} reverse splits on {len(set(s['date'] for s in splits))} unique dates", file=sys.stderr)
    for s in splits:
        print(f"    {s['date']} {s['symbol']} {s['ratio']}:1", file=sys.stderr)

    # Save split events
    (OUT / "split_events.json").write_text(json.dumps(
        [{"date": str(s["date"]), "symbol": s["symbol"], "ratio": s["ratio"]} for s in splits],
        indent=2))

    # SPY B&H benchmark
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
    print(f"  20 — SPLIT SIGNAL (inverse ETF reverse splits → trade)", file=sys.stderr)
    print(f"{'='*100}", file=sys.stderr)
    print(f"  {'Config':<20s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'N':>3s}", file=sys.stderr)
    print(f"  {'-'*20}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*3}", file=sys.stderr)

    for config_name in tqdm(list(CONFIGS.keys()), desc="Configs", file=sys.stderr):
        cfg = CONFIGS[config_name]
        m = run_split_strategy(tape, trading_days, splits, config_name, cfg)
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
        print(f"  {config_name:<20s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.02f}  {full_ret:>+7.0f}%  {mdd:>5.1f}%  {wr:>4.0f}%  {m['n_trades']:>3d}", file=sys.stderr)

    print(f"{'='*100}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for name, m in config_series.items():
        dr = np.array(m["daily_rets"])
        pd.DataFrame({
            "date": m["dates"], "day_ret": m["daily_rets"],
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{name}.parquet", index=False)

    # Primary config: gld_20d (strongest t-stat from analysis)
    primary = "gld_20d"
    pm = config_series[primary]
    metrics = compute_experiment_metrics(pm["daily_rets"], pm["dates"], pm["n_trades"], pm["n_wins"], pm["n_losses"])
    print_results("20 SPLIT SIGNAL (gld_20d)", metrics)
    save_results(OUT, primary, pm["daily_rets"], pm["dates"], metrics, pm["data_gaps"])
    plot_pnl(OUT, "Split Signal (buy GLD 20d after split)", pm["daily_rets"], pm["dates"],
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
