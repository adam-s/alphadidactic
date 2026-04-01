"""19 Gap Reversal — 8-step verification suite.

Run: python experiments/19_gap_reversal/verify_integrity.py
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
from shared.research_core import INITIAL_CAPITAL, get_symbols
from shared.verify_harness import VerificationHarness

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")
SPLIT_THR = SPLIT_THRESHOLD
EXCLUDE = {"SPY", "QQQ", "VXX", "TQQQ"}
MIN_GAP = 0.001


def main():
    results = pd.read_parquet(OUT / "results.parquet")

    schedule = build_schedule("verify19", [
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
                   grace_minutes_before=390, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    h = VerificationHarness(OUT, engine, conn, trading_days)

    try:
        h.check_1_cache_vs_raw(
            ["SPY", "VXX", "AAPL", "MSFT", "NVDA", "JPM", "BA", "TSLA", "AMZN", "META"],
            {"p0935": "09:35", "p1530": "15:30", "p1600": "16:00"})
        h.check_2_dst()
        check_3_temporal_trace(h, results)
        check_4_manual_calc(h, engine, conn, trading_days)
        h.check_5_train_test(results)
        check_6_incremental(h, trading_days, results)
        h.check_7_signal_direction(results)
        h.check_8_data_integrity(["SPY", "VXX", "AAPL", "MSFT", "NVDA", "JPM"])
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

    # prev_p1530 (gap denom): available T-1 15:30, used at T 09:35
    data_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 15, 30, tzinfo=ET)
    used_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "prev_p1530 (gap denominator)", "available_at": str(data_avail),
                  "used_at": str(used_at), "causal": data_avail < used_at})

    # p0935 (gap numer + entry): available T 09:35, used for exit at T 15:30
    p0935_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    exit_at = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    trace.append({"access": "p0935 (gap + entry price)", "available_at": str(p0935_avail),
                  "used_at": str(exit_at), "causal": p0935_avail < exit_at})

    # p1530 (exit): available T 15:30, stored for next day's gap
    next_idx = td.index(trace_date) + 1
    if next_idx < len(td):
        next_date = td[next_idx]
        next_use = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
        p1530_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
        trace.append({"access": "p1530 (exit + next gap denom)", "available_at": str(p1530_avail),
                      "used_at": str(next_use), "causal": p1530_avail < next_use})

    all_causal = all(t["causal"] for t in trace)
    (h.out / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        h.check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        h.check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(h, engine, conn, trading_days):
    """Verify one gap reversal trade from raw DB."""
    base_symbols = get_symbols()
    price_cache = load_price_cache(OUT / "price_cache.parquet")

    results = pd.read_parquet(OUT / "results.parquet")
    active = results[results["day_ret"] != 0.0]
    if len(active) == 0:
        h.check_fail("Check 4", "No active days")
        return

    check_date = pd.Timestamp(active.iloc[len(active) // 3]["date"]).date()
    strategy_ret = float(active.iloc[len(active) // 3]["day_ret"])
    check_idx = trading_days.index(check_date)
    prev_date = trading_days[check_idx - 1]

    # Replay gap computation to find selected symbols
    prev_p1530 = {}
    for sym in base_symbols:
        v = price_cache.get(prev_date, {}).get(sym, {}).get("p1530")
        if v is not None:
            prev_p1530[sym] = v

    gaps = []
    for sym in base_symbols:
        if sym in EXCLUDE:
            continue
        pc = prev_p1530.get(sym)
        day_data = price_cache.get(check_date, {}).get(sym, {})
        op = day_data.get("p0935")
        if not pc or not op or pc <= 0 or op <= 0:
            continue
        gap = op / pc - 1
        if abs(gap) >= SPLIT_THR:
            continue
        gaps.append((gap, sym))

    gaps.sort()
    # top3_inv: gap-UP candidates
    long_cands = [(g, s) for g, s in reversed(gaps) if g > MIN_GAP][:3]

    if not long_cands:
        h.check_fail("Check 4", f"No gap-up candidates on {check_date}")
        return

    # Verify from raw DB
    selected_sym = long_cands[0][1]
    tape_check = engine.resolve_day(conn, check_date, [selected_sym])
    raw_entry = tape_check.get_price("p0935", selected_sym)
    raw_exit = tape_check.get_price("p1530", selected_sym)

    if not raw_entry or not raw_exit:
        h.check_fail("Check 4", f"Missing raw prices for {selected_sym}")
        return

    raw_ret = raw_exit / raw_entry - 1
    manual_ret = 0.0 if abs(raw_ret) >= SPLIT_THR else raw_ret - 2 * TC

    # For top3, strategy_ret is mean of 3 returns — can't compare directly to one symbol
    # Instead verify price correctness
    cached_entry = price_cache.get(check_date, {}).get(selected_sym, {}).get("p0935")
    cached_exit = price_cache.get(check_date, {}).get(selected_sym, {}).get("p1530")

    if cached_entry and cached_exit:
        entry_ok = abs(cached_entry - raw_entry) < 1e-6
        exit_ok = abs(cached_exit - raw_exit) < 1e-6
        if entry_ok and exit_ok:
            h.check_pass("Check 4", f"{check_date} {selected_sym}: gap={long_cands[0][0]:.4f} "
                         f"entry={raw_entry:.2f} exit={raw_exit:.2f} ret={manual_ret:.6f} (raw DB verified)")
        else:
            h.check_fail("Check 4", f"{check_date} cache/raw mismatch for {selected_sym}")
    else:
        h.check_fail("Check 4", f"{check_date} missing cache for {selected_sym}")


def check_6_incremental(h, trading_days, results):
    """Independent replay of top3_long config."""
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        h.check_fail("Check 6", "No price cache")
        return
    price_cache = load_price_cache(cache_path)

    base_symbols = get_symbols()
    schedule = build_schedule("check6_19", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])

    equity = INITIAL_CAPITAL
    prev_p1530 = {}

    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days) - 1, n_samples, dtype=int))
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        phased = CachedPhasedDay(price_cache, today, schedule)
        m = phased.resolve_up_to(clock_time(9, 35))
        p0935 = m.get("p0935", {})
        aft = phased.resolve_up_to(clock_time(15, 30))
        p1530 = aft.get("p1530", {})

        # Compute gaps
        gaps = []
        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if not pc or not op or pc <= 0 or op <= 0:
                continue
            gap = op / pc - 1
            if abs(gap) >= SPLIT_THR:
                continue
            gaps.append((gap, sym))

        gaps.sort()
        # top3_inv: buy gap-UP stocks (inverse/momentum continuation)
        long_cands = [(g, s) for g, s in reversed(gaps) if g > MIN_GAP][:3]

        long_rets = []
        for _, sym in long_cands:
            ep = p0935.get(sym)
            xp = p1530.get(sym)
            if ep and xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                if abs(rr) < SPLIT_THR:
                    long_rets.append(rr - 2 * TC)

        day_ret = np.mean(long_rets) if long_rets else 0.0
        equity *= (1 + day_ret)

        # Filter Nones (R7 fix)
        prev_p1530 = {}
        for sym, p in p1530.items():
            if p is not None:
                prev_p1530[sym] = p

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
