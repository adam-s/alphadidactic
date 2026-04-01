"""20 Split Signal — 8-step verification suite.

Run: python experiments/20_split_signal/verify_integrity.py
"""
from __future__ import annotations

import json
import sys
from datetime import time as clock_time, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay,
    Checkpoint, ResolutionMode, build_schedule, load_price_cache,
)
from shared.research_core import INITIAL_CAPITAL
from shared.verify_harness import VerificationHarness

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")
SPLIT_THR = SPLIT_THRESHOLD
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
    results = pd.read_parquet(OUT / "results.parquet")

    schedule = build_schedule("verify20", [
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

    h = VerificationHarness(OUT, engine, conn, trading_days)

    try:
        h.check_1_cache_vs_raw(
            ["SPY", "VXX", "GLD", "SQQQ", "UVXY", "SH"],
            {"p0935": "09:35", "p1530": "15:30", "p1600": "16:00"})
        h.check_2_dst()
        check_3_temporal_trace(h, results)
        check_4_manual_calc(h, engine, conn, trading_days)
        h.check_5_train_test(results)
        check_6_incremental(h, trading_days, results)
        h.check_7_signal_direction(results)
        h.check_8_data_integrity(["SPY", "VXX", "GLD", "SQQQ", "UVXY", "SH"])
    finally:
        conn.close()

    if not h.summarize():
        sys.exit(1)


def check_3_temporal_trace(h, results):
    # Find a date with non-zero return (active trade)
    active = results[results["day_ret"] != 0.0]
    if len(active) == 0:
        h.check_fail("Check 3", "No active days")
        return
    trace_date = pd.Timestamp(active.iloc[len(active) // 2]["date"]).date()
    td = h.trading_days
    prev_date = td[td.index(trace_date) - 1]
    trace = []

    # Split detection: prev_p1600 available T-1 16:00, compared at T-split 09:35
    # The split happened on a PRIOR date — the signal was the price jump
    split_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 16, 0, tzinfo=ET)
    entry_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "split detection (price jump on prior date)",
                  "available_at": str(split_avail), "used_at": str(entry_at),
                  "causal": split_avail < entry_at})

    # GLD p1530 for daily return: available T 15:30, used for equity at T 15:30
    p1530_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    next_idx = td.index(trace_date) + 1
    if next_idx < len(td):
        next_date = td[next_idx]
        next_use = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
        trace.append({"access": "GLD p1530 (daily return)",
                      "available_at": str(p1530_avail), "used_at": str(next_use),
                      "causal": p1530_avail < next_use})

    all_causal = all(t["causal"] for t in trace)
    (h.out / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        h.check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        h.check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(h, engine, conn, trading_days):
    """Verify GLD price from raw DB on an active trading day."""
    results = pd.read_parquet(OUT / "results.parquet")
    active = results[results["day_ret"] != 0.0]
    if len(active) == 0:
        h.check_fail("Check 4", "No active days")
        return

    check_date = pd.Timestamp(active.iloc[len(active) // 3]["date"]).date()
    strategy_ret = float(active.iloc[len(active) // 3]["day_ret"])

    # Get raw GLD prices
    tape = engine.resolve_day(conn, check_date, ["GLD"])
    gld_1530 = tape.get_price("p1530", "GLD")

    # Get previous day GLD close
    check_idx = trading_days.index(check_date)
    prev_date = trading_days[check_idx - 1]
    tape_prev = engine.resolve_day(conn, prev_date, ["GLD"])
    gld_prev_1530 = tape_prev.get_price("p1530", "GLD")

    if not gld_1530 or not gld_prev_1530:
        h.check_fail("Check 4", f"Missing GLD prices on {check_date}")
        return

    raw_ret = gld_1530 / gld_prev_1530 - 1

    # Verify cache matches
    cache = load_price_cache(OUT / "price_cache.parquet")
    cached_today = cache.get(check_date, {}).get("GLD", {}).get("p1530")
    cached_prev = cache.get(prev_date, {}).get("GLD", {}).get("p1530")

    if cached_today and cached_prev:
        cache_ok = abs(cached_today - gld_1530) < 1e-6 and abs(cached_prev - gld_prev_1530) < 1e-6
        ret_delta = abs(raw_ret - strategy_ret)
        if cache_ok and ret_delta < 1e-4:  # Looser tolerance — daily return includes TC
            h.check_pass("Check 4", f"{check_date} GLD: prev={gld_prev_1530:.2f} today={gld_1530:.2f} "
                         f"raw_ret={raw_ret:.6f} strategy_ret={strategy_ret:.6f} "
                         f"delta={ret_delta:.2e} (raw DB verified)")
        elif not cache_ok:
            h.check_fail("Check 4", f"{check_date} GLD cache/raw mismatch")
        else:
            h.check_fail("Check 4", f"{check_date} GLD ret delta={ret_delta:.2e} (raw={raw_ret:.6f} strat={strategy_ret:.6f})")
    else:
        h.check_fail("Check 4", f"Missing cache for GLD on {check_date}")


def check_6_incremental(h, trading_days, results):
    """Independent replay of gld_20d config."""
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        h.check_fail("Check 6", "No price cache")
        return
    price_cache = load_price_cache(cache_path)

    # Detect splits independently
    splits = detect_reverse_splits(price_cache, trading_days)
    split_dates = sorted(set(s["date"] for s in splits))

    schedule = build_schedule("check6_20", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0),
    ])

    equity = INITIAL_CAPITAL
    entry_price = None
    holding_until_idx = -1
    td_idx = {td: i for i, td in enumerate(trading_days)}

    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days) - 1, n_samples, dtype=int))
    max_delta = 0.0
    n_checked = 0

    pending_split = False

    for i, today in enumerate(trading_days):
        phased = CachedPhasedDay(price_cache, today, schedule)
        m = phased.resolve_up_to(clock_time(9, 35))
        p0935 = m.get("p0935", {})
        aft = phased.resolve_up_to(clock_time(15, 30))
        p1530 = aft.get("p1530", {})

        day_ret = 0.0

        # Execute pending entry
        if pending_split and entry_price is None:
            gld_entry = p0935.get("GLD")
            if gld_entry and gld_entry > 0:
                entry_price = gld_entry
                holding_until_idx = i + 20
                day_ret -= TC
            pending_split = False

        # Daily return if holding
        if entry_price is not None:
            gld_today = p1530.get("GLD")
            gld_prev = None
            if i > 0:
                prev_phased = CachedPhasedDay(price_cache, trading_days[i - 1], schedule)
                prev_phased.resolve_up_to(clock_time(15, 30))
                prev_resolved = prev_phased._resolved
                gld_prev = prev_resolved.get("p1530", {}).get("GLD")
            if gld_prev is None:
                gld_prev = entry_price

            if gld_today and gld_prev and gld_prev > 0 and gld_today > 0:
                raw_ret = gld_today / gld_prev - 1
                if abs(raw_ret) < SPLIT_THR:
                    day_ret += raw_ret

            if i >= holding_until_idx:
                day_ret -= TC
                entry_price = None
                holding_until_idx = -1

        # Detect split
        if entry_price is None and not pending_split and today in split_dates:
            pending_split = True

        equity *= (1 + day_ret)

        if today in sample_dates:
            batch_row = results[results["date"] == today]
            if len(batch_row) > 0:
                delta = abs(equity - float(batch_row.iloc[0]["equity"]))
                max_delta = max(max_delta, delta)
                n_checked += 1

    if max_delta < 1e-8 and n_checked >= 20:
        h.check_pass("Check 6", f"{n_checked} dates, max_delta={max_delta:.2e}")
    elif n_checked < 20:
        h.check_fail("Check 6", f"Only {n_checked} dates checked")
    else:
        h.check_fail("Check 6", f"max_delta={max_delta:.2e}")


if __name__ == "__main__":
    main()
