# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""Experiment 15 — Cross-Sectional Gold: experiment-specific config and helpers."""
from __future__ import annotations

from datetime import time as clock_time

from shared.config import SPLIT_THRESHOLD
from shared.cursor_engine import Checkpoint, ResolutionMode, build_schedule

SPLIT_THR = SPLIT_THRESHOLD

ALL_PM = ["GLD", "GDX", "NUGT", "SLV", "SIL"]

CONFIGS = {
    "nugt_only":    {"instruments": ["NUGT"], "top_n": 1, "ema_span": 34},
    "best1_all5":   {"instruments": ALL_PM, "top_n": 1, "ema_span": 34},
    "best2_all5":   {"instruments": ALL_PM, "top_n": 2, "ema_span": 34},
    "best1_gold3":  {"instruments": ["GLD", "GDX", "NUGT"], "top_n": 1, "ema_span": 34},
    "best1_ema20":  {"instruments": ALL_PM, "top_n": 1, "ema_span": 20},
    "best1_ema10":  {"instruments": ALL_PM, "top_n": 1, "ema_span": 10},
    "nugt_ema10":   {"instruments": ["NUGT"], "top_n": 1, "ema_span": 10},
    "best1_silver": {"instruments": ["SLV", "SIL"], "top_n": 1, "ema_span": 34},
}


def get_schedule():
    return build_schedule("exp15_xsec_gold", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1030", target_time_et=clock_time(10, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])
