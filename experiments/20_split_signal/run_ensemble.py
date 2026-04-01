# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""20 Split Signal — Ensemble: equal-weight GLD + short VXX + SPY after splits.

Combines the 3 positive-Sharpe legs into one portfolio:
  - 1/3 long GLD, hold 20 days
  - 1/3 short VXX, hold 20 days
  - 1/3 long SPY, hold 20 days

All triggered by the same signal: inverse ETF reverse split detected.
Diversification across 3 uncorrelated instruments reduces drawdown.

NOTE: With only ~10 split events over 4 years, this result is NOT
statistically significant. Need 20+ years of data to validate.
The bootstrap CI includes zero. This is exploratory, not definitive.

Usage:
    python experiments/20_split_signal/run_ensemble.py
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
from shared.cursor_engine import load_price_cache
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.metrics import sharpe
from shared.research_core import INITIAL_CAPITAL

OUT = HERE / "output"
SPLIT_THR = SPLIT_THRESHOLD
HOLD_DAYS = 20

# Ensemble legs: equal weight 1/3 each
LEGS = [
    {"instrument": "GLD", "direction": "long",  "weight": 1 / 3},
    {"instrument": "VXX", "direction": "short", "weight": 1 / 3},
    {"instrument": "SPY", "direction": "long",  "weight": 1 / 3},
]

INVERSE_ETFS = ["VXX", "SQQQ", "SPXS", "SH", "SDS", "SOXS", "UVXY", "TZA", "UNG"]
REVERSE_SPLIT_MIN = 2.0


def detect_reverse_splits(price_cache, trading_days):
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


def main():
    from shared.cursor_engine import CursorEngine, MinuteBarsSource, Checkpoint, ResolutionMode, build_schedule

    schedule = build_schedule("exp20_ens", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0),
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0),
    ])
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)
    conn.close()

    cache_path = OUT / "price_cache.parquet"
    price_cache = load_price_cache(cache_path)

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

    splits = detect_reverse_splits(price_cache, trading_days)
    split_dates = sorted(set(s["date"] for s in splits))
    print(f"  {len(splits)} splits on {len(split_dates)} unique dates", file=sys.stderr)

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

    # Run ensemble
    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    n_trades = 0
    n_wins = 0
    n_losses = 0

    # Per-leg state
    leg_entry = [None] * len(LEGS)  # entry price per leg
    leg_until = [-1] * len(LEGS)    # holding_until_idx per leg
    pending_split = False

    for i, today in enumerate(trading_days):
        day_data = tape.get(today, {})
        p0935 = day_data.get("p0935", {})
        p1530 = day_data.get("p1530", {})

        day_ret = 0.0

        # Execute pending entries
        if pending_split and all(ep is None for ep in leg_entry):
            for li, leg in enumerate(LEGS):
                inst = leg["instrument"]
                ep = p0935.get(inst)
                if ep and ep > 0:
                    leg_entry[li] = ep
                    leg_until[li] = i + HOLD_DAYS
            day_ret -= TC  # Entry TC (one round)
            pending_split = False

        # Compute weighted daily return from all active legs
        for li, leg in enumerate(LEGS):
            if leg_entry[li] is None:
                continue
            inst = leg["instrument"]
            wt = leg["weight"]
            direction = 1.0 if leg["direction"] == "long" else -1.0

            inst_today = p1530.get(inst)
            inst_prev = None
            if i > 0:
                prev_data = tape.get(trading_days[i - 1], {})
                inst_prev = prev_data.get("p1530", {}).get(inst)
            if inst_prev is None:
                inst_prev = leg_entry[li]

            if inst_today and inst_prev and inst_prev > 0 and inst_today > 0:
                raw_ret = inst_today / inst_prev - 1
                if abs(raw_ret) < SPLIT_THR:
                    day_ret += direction * raw_ret * wt

        # Check if any leg exits
        any_exit = False
        for li in range(len(LEGS)):
            if leg_entry[li] is not None and i >= leg_until[li]:
                # Track win/loss on this leg
                inst = LEGS[li]["instrument"]
                exit_p = p1530.get(inst)
                if exit_p and leg_entry[li] > 0 and exit_p > 0:
                    direction = 1.0 if LEGS[li]["direction"] == "long" else -1.0
                    leg_ret = direction * (exit_p / leg_entry[li] - 1)
                    n_trades += 1
                    if leg_ret > 0:
                        n_wins += 1
                    elif leg_ret < 0:
                        n_losses += 1
                leg_entry[li] = None
                leg_until[li] = -1
                any_exit = True

        if any_exit:
            day_ret -= TC  # Exit TC

        # Detect split
        if all(ep is None for ep in leg_entry) and not pending_split:
            if today in split_dates:
                pending_split = True

        equity *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

    # Results
    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    tts = pd.Timestamp(TRAIN_END)
    cum = np.cumprod(1 + dr)
    pk = np.maximum.accumulate(cum)
    mdd = float(abs((cum / pk - 1).min()) * 100)
    full_ret = float((np.prod(1 + dr) - 1) * 100)

    metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
    print_results("20 SPLIT ENSEMBLE (GLD + short VXX + SPY)", metrics)
    save_results(OUT, "ensemble", daily_rets, dates, metrics, [])
    plot_pnl(OUT, "Split Ensemble (1/3 GLD + 1/3 short VXX + 1/3 SPY)",
             daily_rets, dates, trading_days, spy_day_rets, metrics, color="#059669")

    # Save ensemble parquet
    pd.DataFrame({
        "date": dates, "day_ret": daily_rets,
        "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
    }).to_parquet(OUT / "ensemble.parquet", index=False)

    # Stat tests
    from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
    spy_all = np.array([spy_day_rets.get(d, 0.0) for d in dates])
    stat_results = {
        "permutation": permutation_test(dr, spy_all, tc_per_active_day=2 * TC),
        "bootstrap": bootstrap_sharpe_ci(dr),
        "concentration": concentration_ratio(dr),
    }
    (OUT / "ensemble_stat_tests.json").write_text(json.dumps(stat_results, indent=2, default=str))
    for sname, res in stat_results.items():
        status = "PASS" if res.get("pass") else "FAIL" if res.get("pass") is False else "N/A"
        print(f"  {sname}: {status} — {res.get('interpretation', '')}", file=sys.stderr)

    # Ensemble results
    (OUT / "ensemble_results.json").write_text(json.dumps({
        "train_sh": metrics["train_sharpe"],
        "test_sh": metrics["test_sharpe"],
        "ret": round(full_ret, 1),
        "dd": round(mdd, 2),
        "n_trades": n_trades,
        "legs": ["GLD long", "VXX short", "SPY long"],
        "note": "NOT statistically significant with ~10 events. Need 20+ years of data.",
    }, indent=2))


if __name__ == "__main__":
    main()
