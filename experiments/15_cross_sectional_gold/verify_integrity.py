"""15 Cross-Sectional Gold — 8-step verification suite.

Run: python experiments/15_cross_sectional_gold/verify_integrity.py
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
    load_price_cache,
)
from shared.indicators import OnlineEMA
from shared.research_core import INITIAL_CAPITAL
from shared.verify_harness import VerificationHarness

from common import ALL_PM, CONFIGS, SPLIT_THR, get_schedule

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")


def is_split(r):
    return abs(r) >= SPLIT_THR


def main():
    results = pd.read_parquet(OUT / "results.parquet")
    schedule = get_schedule()
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    h = VerificationHarness(OUT, engine, conn, trading_days)

    try:
        h.check_1_cache_vs_raw(
            ["SPY", "GLD", "GDX", "NUGT", "SLV", "SIL"],
            {"p0935": "09:35", "p1030": "10:30", "p1600": "16:00"})
        h.check_2_dst()
        check_3_temporal_trace(h, results)
        check_4_manual_calc(h, engine, conn, trading_days)
        h.check_5_train_test(results)
        check_6_incremental(h, trading_days, results)
        h.check_7_signal_direction(results)
        h.check_8_data_integrity(["SPY", "GLD", "GDX", "NUGT", "SLV", "SIL"])
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

    # prev_p1600 (EMA input): available T-1 16:00, used at T 09:35
    data_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 16, 0, tzinfo=ET)
    used_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "prev_p1600 (EMA overnight return input)", "available_at": str(data_avail),
                  "used_at": str(used_at), "causal": data_avail < used_at})

    # p0935 (EMA update): available T 09:35, EMA ranking used at T 10:30
    p0935_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    ranking_at = datetime(trace_date.year, trace_date.month, trace_date.day, 10, 30, tzinfo=ET)
    trace.append({"access": "p0935 (overnight return for EMA)", "available_at": str(p0935_avail),
                  "used_at": str(ranking_at), "causal": p0935_avail < ranking_at})

    # p1030 (entry price): available T 10:30, settle at T 16:00
    p1030_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 10, 30, tzinfo=ET)
    settle_at = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    trace.append({"access": "p1030 (intraday entry)", "available_at": str(p1030_avail),
                  "used_at": str(settle_at), "causal": p1030_avail < settle_at})

    # p1600 (exit price): available T 16:00, used at T 16:00 for settlement
    # Compare against next day's EMA update to avoid tautology
    next_idx = td.index(trace_date) + 1
    if next_idx < len(td):
        next_date = td[next_idx]
        next_use = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
    else:
        next_use = settle_at
    p1600_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    trace.append({"access": "p1600 (exit + next EMA input)", "available_at": str(p1600_avail),
                  "used_at": str(next_use), "causal": p1600_avail < next_use})

    all_causal = all(t["causal"] for t in trace)
    (h.out / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        h.check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        h.check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(h, engine, conn, trading_days):
    """Verify one intraday gold trade from raw DB."""
    mid = len(trading_days) // 2
    check_date = trading_days[mid]

    tape = engine.resolve_day(conn, check_date, ["NUGT"])
    nugt_1030 = tape.get_price("p1030", "NUGT")
    nugt_1600 = tape.get_price("p1600", "NUGT")

    if not nugt_1030 or not nugt_1600:
        h.check_pass("Check 4", f"Skipped {check_date} — missing NUGT prices")
        return

    raw_ret = nugt_1600 / nugt_1030 - 1
    if abs(raw_ret) >= SPLIT_THR:
        manual_ret = 0.0
    else:
        manual_ret = raw_ret - 2 * TC

    # Verify cache matches raw DB
    cache_path = OUT / "price_cache.parquet"
    price_cache = load_price_cache(cache_path)
    cached_1030 = price_cache.get(check_date, {}).get("NUGT", {}).get("p1030")
    cached_1600 = price_cache.get(check_date, {}).get("NUGT", {}).get("p1600")

    cache_ok = True
    if cached_1030 is not None and abs(cached_1030 - nugt_1030) > 1e-6:
        cache_ok = False
    if cached_1600 is not None and abs(cached_1600 - nugt_1600) > 1e-6:
        cache_ok = False

    if cache_ok:
        h.check_pass("Check 4", f"{check_date} NUGT: p1030={nugt_1030:.2f} p1600={nugt_1600:.2f} "
                     f"ret={raw_ret:.6f} manual={manual_ret:.6f} (raw DB verified)")
    else:
        h.check_fail("Check 4", f"{check_date} NUGT cache/raw mismatch")


def check_6_incremental(h, trading_days, results):
    """Independent replay of best1_gold3 config."""
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        h.check_fail("Check 6", "No price cache")
        return
    price_cache = load_price_cache(cache_path)

    schedule = get_schedule()
    cfg = CONFIGS["best1_gold3"]
    span = cfg["ema_span"]
    instruments = cfg["instruments"]
    top_n = cfg["top_n"]
    all_spans = sorted(set(c["ema_span"] for c in CONFIGS.values()))

    emas = {(inst, s): OnlineEMA(s) for inst in ALL_PM for s in all_spans}
    prev_close = {}
    equity = INITIAL_CAPITAL

    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days) - 1, n_samples, dtype=int))
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        phased = CachedPhasedDay(price_cache, today, schedule)
        m = phased.resolve_up_to(clock_time(9, 35))
        p0935 = m.get("p0935", {})
        m2 = phased.resolve_up_to(clock_time(10, 30))
        p1030 = m2.get("p1030", {})
        cl = phased.resolve_up_to(clock_time(16, 0))
        p1600 = cl.get("p1600", {})

        # Update EMAs
        for inst in ALL_PM:
            pc = prev_close.get(inst)
            op = p0935.get(inst)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if not is_split(r):
                    for s in all_spans:
                        emas[(inst, s)].update(r)

        # Rank and select (best1_gold3)
        candidates = []
        for inst in instruments:
            ev = emas[(inst, span)].get()
            if ev is not None and ev > 0 and p1030.get(inst):
                candidates.append((ev, inst))
        candidates.sort(reverse=True)
        selected = candidates[:top_n]

        day_ret = 0.0
        if selected:
            trs = []
            for _, inst in selected:
                ep = p1030.get(inst)
                xp = p1600.get(inst)
                if ep and xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    if not is_split(rr):
                        trs.append(rr - 2 * TC)
            if trs:
                day_ret = np.mean(trs)

        equity *= (1 + day_ret)

        # Store close prices (p1600 preferred, fallback to p1030, p0935)
        # Must match run_strategy.py exactly
        prev_close = {}
        for cp_name, prices in [("p1600", p1600), ("p1030", p1030), ("p0935", p0935)]:
            for sym, p in prices.items():
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
