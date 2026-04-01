"""17 Base Rejects — Optuna parameter optimization.

Optimizes reject_pctile for the reject gate using train-only objective.

Usage:
    python experiments/17_base_rejects/run_optuna.py
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
from shared.research_core import FRED_PANEL_PATH, MacroRegime, get_symbols

from run_strategy import run_reject, CONFIGS, OUT, SPLIT_THR, EXCLUDE

# Pre-load data
print("  Loading data...", file=sys.stderr)
base_symbols = get_symbols()
price_cache = load_price_cache(OUT / "price_cache.parquet")

regime_df = pd.read_parquet(OUT / "regime_cache.parquet")
regime_by_day = {}
for _, row in regime_df.iterrows():
    d = row["date"]
    if hasattr(d, "date") and callable(getattr(d, "date", None)):
        d = d.date()
    elif hasattr(d, "astype"):
        d = pd.Timestamp(d).date()
    regime_by_day[d] = str(row["regime"])

schedule = build_schedule("optuna17", [
    Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
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

print("  Data loaded.", file=sys.stderr)


def objective_fn(params: dict) -> dict:
    cfg = {"mode": "reject", "reject_pctile": params["reject_pctile"]}
    m = run_reject(tape, trading_days, regime_by_day, base_symbols, "optuna", cfg,
                   reject_pctile_override=params["reject_pctile"])
    dr = np.array(m["daily_rets"])
    dt = pd.to_datetime(m["dates"])
    tts = pd.Timestamp(TRAIN_END)
    return {
        "train_sharpe": float(sharpe(dr[dt <= tts])),
        "test_sharpe": float(sharpe(dr[dt > tts])),
    }


def param_space(trial) -> dict:
    return {
        "reject_pctile": trial.suggest_float("reject_pctile", 0.0, 0.74),
    }


if __name__ == "__main__":
    result = run_optimization(
        objective_fn=objective_fn,
        param_space=param_space,
        train_metric="train_sharpe",
        test_metric="test_sharpe",
        seed_params=[{"reject_pctile": 0.50}],
        n_trials=150,
        log_path=OUT / "optuna_log.csv",
        study_name="base_rejects_pctile",
    )

    save_optuna_results(result, OUT)
    overfitting = analyze_overfitting(result)
    (OUT / "optuna_overfitting.json").write_text(json.dumps(overfitting, indent=2, default=str))

    print(f"\n{'='*80}", file=sys.stderr)
    print(f"  OPTUNA RESULTS: 17 Base Rejects", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
    print(f"  Trials: {result['n_trials']}", file=sys.stderr)
    print(f"  Best trial: #{result['convergence_trial']}", file=sys.stderr)
    print(f"  Best reject_pctile: {result['best_params']['reject_pctile']:.4f}", file=sys.stderr)
    print(f"  Best train Sharpe: {result['best_value']:.3f}", file=sys.stderr)
    if result.get("train_test_gap") is not None:
        print(f"  Train-test gap: {result['train_test_gap']:+.3f}", file=sys.stderr)
    if result.get("seed_metrics"):
        seed_train = result["seed_metrics"][0]["train_sharpe"]
        print(f"  Seed train Sharpe: {seed_train:.3f}", file=sys.stderr)
        print(f"  Improvement: {result['best_value'] - seed_train:+.3f}", file=sys.stderr)
    if "recommended_params" in overfitting:
        print(f"  Recommended (Pareto): reject_pctile={overfitting['recommended_params']['reject_pctile']:.4f}", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
