"""18 Dual Intraday — Optuna parameter optimization.

Optimizes split_hour for dual intraday compounding.

Usage:
    python experiments/18_dual_intraday/run_optuna.py
"""
from __future__ import annotations

import json
import sys
from datetime import time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import TRAIN_END, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, Checkpoint, ResolutionMode,
    build_schedule, load_price_cache,
)
from shared.metrics import sharpe
from shared.optuna_utils import run_optimization, save_optuna_results, analyze_overfitting

from run_strategy import run_dual, CONFIGS, OUT

# Pre-load data
print("  Loading data...", file=sys.stderr)
price_cache = load_price_cache(OUT / "price_cache.parquet")

all_times = sorted({clock_time(9, 35), clock_time(10, 30), clock_time(12, 30),
                    clock_time(13, 0), clock_time(13, 30), clock_time(14, 0), clock_time(16, 0)})
checkpoints = [
    Checkpoint(name=f"p{t.hour:02d}{t.minute:02d}", target_time_et=t,
               mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
               grace_minutes_after=0, required=False, trading_day_offset=0)
    for t in all_times]

schedule = build_schedule("optuna18", checkpoints)
engine = CursorEngine(MinuteBarsSource(), schedule)
conn = engine.source.get_connection()
trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)
conn.close()

# Build tape
tape = {}
for td in trading_days:
    day_data = price_cache.get(td, {})
    day_prices = {}
    for cp in checkpoints:
        prices = {}
        for sym in ["GLD", "GDX", "NUGT", "SPY", "VXX"]:
            v = day_data.get(sym, {}).get(cp.name)
            if v is not None:
                prices[sym] = v
        day_prices[cp.name] = prices
    tape[td] = day_prices

print("  Data loaded.", file=sys.stderr)

# Map split_hour float → clock_time
SPLIT_TIMES = {
    12.5: clock_time(12, 30),
    13.0: clock_time(13, 0),
    13.5: clock_time(13, 30),
    14.0: clock_time(14, 0),
}


def objective_fn(params: dict) -> dict:
    split_hour = params["split_hour"]
    # Snap to nearest valid split time
    nearest = min(SPLIT_TIMES.keys(), key=lambda x: abs(x - split_hour))
    split_time = SPLIT_TIMES[nearest]

    cfg = {"split": split_time, "entry": clock_time(10, 30), "exit": clock_time(16, 0)}
    m = run_dual(tape, trading_days, "optuna", cfg, split_override=split_time)

    dr = np.array(m["daily_rets"])
    dt = pd.to_datetime(m["dates"])
    tts = pd.Timestamp(TRAIN_END)
    return {
        "train_sharpe": float(sharpe(dr[dt <= tts])),
        "test_sharpe": float(sharpe(dr[dt > tts])),
    }


def param_space(trial) -> dict:
    return {
        "split_hour": trial.suggest_categorical("split_hour", [12.5, 13.0, 13.5, 14.0]),
    }


if __name__ == "__main__":
    result = run_optimization(
        objective_fn=objective_fn,
        param_space=param_space,
        train_metric="train_sharpe",
        test_metric="test_sharpe",
        seed_params=[{"split_hour": 13.0}],
        n_trials=50,  # Only 4 categorical values — 50 is plenty
        log_path=OUT / "optuna_log.csv",
        study_name="dual_intraday_split",
    )

    save_optuna_results(result, OUT)
    overfitting = analyze_overfitting(result)
    (OUT / "optuna_overfitting.json").write_text(json.dumps(overfitting, indent=2, default=str))

    print(f"\n{'='*80}", file=sys.stderr)
    print(f"  OPTUNA RESULTS: 18 Dual Intraday", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
    print(f"  Trials: {result['n_trials']}", file=sys.stderr)
    print(f"  Best split_hour: {result['best_params']['split_hour']}", file=sys.stderr)
    print(f"  Best train Sharpe: {result['best_value']:.3f}", file=sys.stderr)
    if result.get("train_test_gap") is not None:
        print(f"  Train-test gap: {result['train_test_gap']:+.3f}", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
