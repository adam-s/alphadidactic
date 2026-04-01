# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""Experiment 14 — Capital Allocation Fix: experiment-specific config and helpers."""
from __future__ import annotations

from datetime import time as clock_time
from pathlib import Path

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE
from shared.cursor_engine import Checkpoint, ResolutionMode, build_schedule

EXCLUDE = {"SPY", "QQQ", "VXX"}
SPLIT_THR = SPLIT_THRESHOLD

# Base overnight params (original, not Optuna)
STREAK = 0.75
HR_THR = 0.57
LB = 80
PCTILE = 0.50
MIN_IRET = 0.013

# Gold params
GOLD_ID_EMA = 34
GOLD_ON_EMA = 16
VXX_LB = 20

# Flow params
FLOW_EMA = 10
BASE_N = 5
FLOW_N = 5

# Spike params
VXX_SPIKE_THR = 0.03
SQQQ_THR = 0.03

# Cash sweep (Robinhood Gold 3.35% APY)
CASH_APY = 0.0335
CASH_DAILY_RATE = (1 + CASH_APY) ** (1 / 365) - 1
CAL_PER_TRADING = 365.25 / 252

# 6 capital allocation configs
FIX_MODES = ["bugged", "split_80_20", "split_50_50", "base_priority", "gold_priority", "dynamic"]

# Flow cache location
# NOTE: The reference (195) resolved HERE.parent / "flow_cache/output" which
# pointed to reference_experiments/flow_cache/output/ — a path that doesn't exist.
# So the reference ran with EMPTY flow data (load_inst_flow returned {}).
# To match the reference exactly, we use the same non-existent relative path.
# If flow data is desired in the future, point to the repo-root flow_cache.
FLOW_CACHE_DIR = Path(__file__).resolve().parent.parent / "flow_cache" / "output"


def get_schedule():
    return build_schedule("exp14_capfix", [
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
        # p1600: 8-min grace (reference used 8, not 5 — matches close auction window)
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=8, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])
