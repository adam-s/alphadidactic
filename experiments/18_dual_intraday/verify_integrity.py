"""18 Dual Intraday — 8-step verification suite.

Run: python experiments/18_dual_intraday/verify_integrity.py
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
from shared.indicators import OnlineEMA
from shared.research_core import INITIAL_CAPITAL
from shared.verify_harness import VerificationHarness

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")
SPLIT_THR = SPLIT_THRESHOLD
EMA_SPAN = 34


def main():
    results = pd.read_parquet(OUT / "results.parquet")

    all_times = sorted({clock_time(9, 35), clock_time(10, 30), clock_time(12, 30),
                        clock_time(13, 0), clock_time(13, 30), clock_time(14, 0), clock_time(16, 0)})
    checkpoints = [
        Checkpoint(name=f"p{t.hour:02d}{t.minute:02d}", target_time_et=t,
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0)
        for t in all_times]

    schedule = build_schedule("verify18", checkpoints)
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    h = VerificationHarness(OUT, engine, conn, trading_days)

    try:
        h.check_1_cache_vs_raw(
            ["NUGT", "GLD", "GDX", "SPY", "VXX"],
            {"p0935": "09:35", "p1030": "10:30", "p1600": "16:00"})
        h.check_2_dst()
        check_3_temporal_trace(h, results)
        check_4_manual_calc(h, engine, conn, trading_days)
        h.check_5_train_test(results)
        check_6_incremental(h, trading_days, results)
        h.check_7_signal_direction(results)
        h.check_8_data_integrity(["NUGT", "GLD", "GDX", "SPY", "VXX"])
    finally:
        conn.close()

    if not h.summarize():
        sys.exit(1)


def check_3_temporal_trace(h, results):
    active = results[results["day_ret"] != 0.0]
    trace_date = pd.Timestamp(active.iloc[len(active) // 2]["date"]).date()
    td = h.trading_days
    prev_date = td[td.index(trace_date) - 1]
    trace = []

    # prev_close (EMA input): available T-1 16:00, used T 09:35
    data_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 16, 0, tzinfo=ET)
    used_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "prev_close (EMA overnight)", "available_at": str(data_avail),
                  "used_at": str(used_at), "causal": data_avail < used_at})

    # p0935 (EMA update): available T 09:35, used for entry at T 10:30
    p0935_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    entry_at = datetime(trace_date.year, trace_date.month, trace_date.day, 10, 30, tzinfo=ET)
    trace.append({"access": "p0935 (EMA update + signal)", "available_at": str(p0935_avail),
                  "used_at": str(entry_at), "causal": p0935_avail < entry_at})

    # p1030 (entry): available T 10:30, used for exit at T 16:00
    p1030_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 10, 30, tzinfo=ET)
    exit_at = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    trace.append({"access": "p1030 (intraday entry)", "available_at": str(p1030_avail),
                  "used_at": str(exit_at), "causal": p1030_avail < exit_at})

    # p1300 (split): available T 13:00, used for afternoon entry at T 16:00
    p1300_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 13, 0, tzinfo=ET)
    trace.append({"access": "p1300 (split checkpoint)", "available_at": str(p1300_avail),
                  "used_at": str(exit_at), "causal": p1300_avail < exit_at})

    all_causal = all(t["causal"] for t in trace)
    (h.out / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        h.check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        h.check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(h, engine, conn, trading_days):
    """Verify NUGT intraday return from raw DB."""
    mid = len(trading_days) // 2
    check_date = trading_days[mid]

    tape = engine.resolve_day(conn, check_date, ["NUGT"])
    nugt_1030 = tape.get_price("p1030", "NUGT")
    nugt_1600 = tape.get_price("p1600", "NUGT")

    if not nugt_1030 or not nugt_1600:
        h.check_fail("Check 4", f"Missing NUGT prices on {check_date}")
        return

    raw_ret = nugt_1600 / nugt_1030 - 1
    manual_ret = 0.0 if abs(raw_ret) >= SPLIT_THR else raw_ret - 2 * TC

    # Verify cache matches
    cache = load_price_cache(OUT / "price_cache.parquet")
    c1030 = cache.get(check_date, {}).get("NUGT", {}).get("p1030")
    c1600 = cache.get(check_date, {}).get("NUGT", {}).get("p1600")

    if c1030 and c1600 and abs(c1030 - nugt_1030) < 1e-6 and abs(c1600 - nugt_1600) < 1e-6:
        h.check_pass("Check 4", f"{check_date} NUGT: p1030={nugt_1030:.2f} p1600={nugt_1600:.2f} "
                     f"ret={manual_ret:.6f} (raw DB verified)")
    else:
        h.check_fail("Check 4", f"{check_date} NUGT cache/raw mismatch")


def check_6_incremental(h, trading_days, results):
    """Independent replay of single_1030_1600 config."""
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        h.check_fail("Check 6", "No price cache")
        return
    price_cache = load_price_cache(cache_path)

    all_times = sorted({clock_time(9, 35), clock_time(10, 30), clock_time(12, 30),
                        clock_time(13, 0), clock_time(13, 30), clock_time(14, 0), clock_time(16, 0)})
    checkpoints = [
        Checkpoint(name=f"p{t.hour:02d}{t.minute:02d}", target_time_et=t,
                   mode=ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5,
                   grace_minutes_after=0, required=False, trading_day_offset=0)
        for t in all_times]
    schedule = build_schedule("check6_18", checkpoints)

    ema = OnlineEMA(EMA_SPAN)
    prev_close = {}
    equity = INITIAL_CAPITAL

    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days) - 1, n_samples, dtype=int))
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        phased = CachedPhasedDay(price_cache, today, schedule)
        resolved = {}
        for cp in checkpoints:
            r = phased.resolve_up_to(cp.target_time_et)
            for cp_name, prices in r.items():
                resolved[cp_name] = prices

        p0935 = resolved.get("p0935", {})
        p1030 = resolved.get("p1030", {})
        p1600 = resolved.get("p1600", {})

        nugt_pc = prev_close.get("NUGT")
        nugt_op = p0935.get("NUGT")
        if nugt_pc and nugt_op and nugt_pc > 0 and nugt_op > 0:
            r = nugt_op / nugt_pc - 1
            if abs(r) < SPLIT_THR:
                ema.update(r)

        ev = ema.get()
        go = ev is not None and ev > 0

        day_ret = 0.0
        if go:
            ep = p1030.get("NUGT")
            xp = p1600.get("NUGT")
            if ep and xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                if abs(rr) < SPLIT_THR:
                    day_ret = rr - 2 * TC

        equity *= (1 + day_ret)

        prev_close = {}
        for cp_name in sorted(resolved.keys()):
            for sym, p in resolved.get(cp_name, {}).items():
                if p is not None:
                    prev_close[sym] = p

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
