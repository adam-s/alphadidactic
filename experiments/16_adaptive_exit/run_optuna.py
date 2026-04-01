"""16 Adaptive Exit — Optuna parameter optimization.

Optimizes gap_threshold for the adaptive exit strategy using train-only objective.
Uses shared/optuna_utils.run_optimization() — first experiment to use the shared API.

Usage:
    python experiments/16_adaptive_exit/run_optuna.py
"""
from __future__ import annotations

import json
import sys
from datetime import time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import TRAIN_END, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, Checkpoint, ResolutionMode,
    build_schedule, load_price_cache,
)
from shared.metrics import sharpe
from shared.optuna_utils import run_optimization, save_optuna_results, analyze_overfitting
from shared.research_core import FRED_PANEL_PATH, MacroRegime, get_symbols

from run_strategy import run_adaptive, CONFIGS, OUT, SPLIT_THR, EXCLUDE

# Pre-load data once (expensive)
print("  Loading data...", file=sys.stderr)
base_symbols = get_symbols()
all_symbols = sorted(set(base_symbols + ["SPY", "VXX"]))
fred_panel = pd.read_parquet(FRED_PANEL_PATH)
fred_panel.index = pd.to_datetime(fred_panel.index)

schedule = build_schedule("optuna16", [
    Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
               mode=ResolutionMode.AT_OR_BEFORE,
               grace_minutes_before=5, grace_minutes_after=0,
               required=False, trading_day_offset=0),
    Checkpoint(name="p1030", target_time_et=clock_time(10, 30),
               mode=ResolutionMode.AT_OR_BEFORE,
               grace_minutes_before=5, grace_minutes_after=0,
               required=False, trading_day_offset=0),
    Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
               mode=ResolutionMode.AT_OR_BEFORE,
               grace_minutes_before=5, grace_minutes_after=0,
               required=False, trading_day_offset=0),
])

engine = CursorEngine(MinuteBarsSource(), schedule)
conn = engine.source.get_connection()
trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

cache_path = OUT / "price_cache.parquet"
price_cache = load_price_cache(cache_path)

# Load pre-computed regime
regime_df = pd.read_parquet(OUT / "regime_cache.parquet")
regime_by_day = {}
for _, row in regime_df.iterrows():
    d = row["date"]
    if hasattr(d, "date") and callable(getattr(d, "date", None)):
        d = d.date()
    elif hasattr(d, "astype"):
        d = pd.Timestamp(d).date()
    regime_by_day[d] = str(row["regime"])

conn.close()

# Build tape
tape = {}
for td in trading_days:
    day_data = price_cache.get(td, {})
    p0935 = {}; p1030 = {}; p1530 = {}
    for sym, prices in day_data.items():
        for cp, target in [("p0935", p0935), ("p1030", p1030), ("p1530", p1530)]:
            v = prices.get(cp)
            if v is not None:
                target[sym] = v
    tape[td] = {"p0935": p0935, "p1030": p1030, "p1530": p1530}

print("  Data loaded.", file=sys.stderr)


def objective_fn(params: dict) -> dict:
    """Run adaptive strategy with given gap_threshold, return metrics."""
    cfg = {"mode": "adaptive", "gap_threshold": params["gap_threshold"]}
    m = run_adaptive(tape, trading_days, regime_by_day, base_symbols, "optuna", cfg,
                     gap_threshold_override=params["gap_threshold"])

    dr = np.array(m["daily_rets"])
    dt = pd.to_datetime(m["dates"])
    tts = pd.Timestamp(TRAIN_END)

    return {
        "train_sharpe": float(sharpe(dr[dt <= tts])),
        "test_sharpe": float(sharpe(dr[dt > tts])),
    }


def param_space(trial) -> dict:
    """Define the search space."""
    return {
        "gap_threshold": trial.suggest_float("gap_threshold", 0.0, 0.03),
    }


if __name__ == "__main__":
    result = run_optimization(
        objective_fn=objective_fn,
        param_space=param_space,
        train_metric="train_sharpe",
        test_metric="test_sharpe",
        seed_params=[{"gap_threshold": 0.005}],  # adaptive_05pct baseline
        n_trials=150,
        log_path=OUT / "optuna_log.csv",
        study_name="adaptive_exit_gap",
    )

    save_optuna_results(result, OUT)

    # Overfitting analysis
    overfitting = analyze_overfitting(result)
    (OUT / "optuna_overfitting.json").write_text(json.dumps(overfitting, indent=2, default=str))

    # Summary
    print(f"\n{'='*80}", file=sys.stderr)
    print(f"  OPTUNA RESULTS: 16 Adaptive Exit", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
    print(f"  Trials: {result['n_trials']}", file=sys.stderr)
    print(f"  Best trial: #{result['convergence_trial']}", file=sys.stderr)
    print(f"  Best gap_threshold: {result['best_params']['gap_threshold']:.4f}", file=sys.stderr)
    print(f"  Best train Sharpe: {result['best_value']:.3f}", file=sys.stderr)
    if result.get("train_test_gap") is not None:
        print(f"  Train-test gap: {result['train_test_gap']:+.3f}", file=sys.stderr)
    if result.get("seed_metrics"):
        seed_train = result["seed_metrics"][0]["train_sharpe"]
        print(f"  Seed train Sharpe: {seed_train:.3f}", file=sys.stderr)
        print(f"  Improvement: {result['best_value'] - seed_train:+.3f}", file=sys.stderr)
    if "recommended_params" in overfitting:
        print(f"  Recommended (Pareto): gap_threshold={overfitting['recommended_params']['gap_threshold']:.4f}", file=sys.stderr)
        print(f"  Recommended train: {overfitting['recommended_objective']:.3f}, gap: {overfitting['recommended_gap']:+.3f}", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
