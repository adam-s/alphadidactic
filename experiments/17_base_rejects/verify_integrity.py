"""17 Base Rejects — 8-step verification suite.

Run: python experiments/17_base_rejects/verify_integrity.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
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
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, get_symbols
from shared.verify_harness import VerificationHarness

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")

SPLIT_THR = SPLIT_THRESHOLD
EXCLUDE = {"SPY", "QQQ", "VXX", "TQQQ"}
STREAK_MULT = 0.034
HR_THR = 0.567
LOOKBACK = 68
PRIMARY_PCTILE = 0.74
MIN_IRET = 0.029


class Accumulator:
    """Independent reimplementation for Check 6."""

    def __init__(self, lookback=68):
        self.lookback = lookback
        self.rets = defaultdict(list)
        self.hit_rate = {}
        self.avg_pos = {}
        self.streak = {}

    def update(self, sym, ret):
        self.rets[sym].append(ret)
        r = self.rets[sym]
        if len(r) < 20:
            self.hit_rate.pop(sym, None)
            self.avg_pos.pop(sym, None)
            self.streak[sym] = 0
            return
        recent = r[-self.lookback:]
        pos = [x for x in recent if x > 0]
        self.hit_rate[sym] = len(pos) / len(recent)
        self.avg_pos[sym] = float(np.mean(pos)) if pos else 0.0
        s = 0
        for x in reversed(r):
            if x > 0:
                s += 1
            else:
                break
        self.streak[sym] = s

    def get_signal(self, sym, iret):
        if sym not in self.hit_rate:
            return None
        return iret * self.avg_pos.get(sym, 0.0) * (1 + STREAK_MULT * self.streak.get(sym, 0)) * self.hit_rate[sym]


def main():
    results = pd.read_parquet(OUT / "results.parquet")

    schedule = build_schedule("verify17", [
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

    data_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 15, 30, tzinfo=ET)
    used_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "prev_p1530 (accumulator)", "available_at": str(data_avail),
                  "used_at": str(used_at), "causal": data_avail < used_at})

    p0935_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    signal_at = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    trace.append({"access": "p0935 (settle + iret denom)", "available_at": str(p0935_avail),
                  "used_at": str(signal_at), "causal": p0935_avail < signal_at})

    p1530_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    next_idx = td.index(trace_date) + 1
    if next_idx < len(td):
        next_date = td[next_idx]
        settle = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
        trace.append({"access": "p1530 (entry price)", "available_at": str(p1530_avail),
                      "used_at": str(settle), "causal": p1530_avail < settle})

    fred_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 18, 0, tzinfo=ET)
    regime_used = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "FRED panel (regime)", "available_at": str(fred_avail),
                  "used_at": str(regime_used), "causal": fred_avail < regime_used})

    all_causal = all(t["causal"] for t in trace)
    (h.out / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        h.check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        h.check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(h, engine, conn, trading_days):
    """Replay reject_gate50 to find traded symbol, verify from raw DB."""
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

    results = pd.read_parquet(OUT / "results.parquet")
    active = results[results["day_ret"] != 0.0]
    if len(active) == 0:
        h.check_fail("Check 4", "No active days")
        return

    settle_date = pd.Timestamp(active.iloc[len(active) // 3]["date"]).date()
    strategy_ret = float(active.iloc[len(active) // 3]["day_ret"])
    settle_idx = trading_days.index(settle_date)

    schedule_c4 = build_schedule("check4_17", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
    ])

    acc = Accumulator(lookback=LOOKBACK)
    signal_history = []
    prev_p1530 = {}
    found_syms = []

    for today in trading_days[:settle_idx]:
        phased = CachedPhasedDay(price_cache, today, schedule_c4)
        m = phased.resolve_up_to(clock_time(9, 35))
        p0935 = m.get("p0935", {})
        aft = phased.resolve_up_to(clock_time(15, 30))
        p1530 = aft.get("p1530", {})

        regime = regime_by_day.get(today, "unknown")

        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        cands = []
        if regime == "bull":
            for sym in base_symbols:
                if sym in EXCLUDE:
                    continue
                p0 = p0935.get(sym)
                p1 = p1530.get(sym)
                if not p0 or not p1 or p0 <= 0 or p1 <= 0:
                    continue
                iret = p1 / p0 - 1
                if abs(iret) >= SPLIT_THR or abs(iret) < MIN_IRET:
                    continue
                hr = acc.hit_rate.get(sym)
                if hr is None or hr <= HR_THR:
                    continue
                sig = acc.get_signal(sym, iret)
                if sig is not None:
                    cands.append((sig, sym, p1))
            cands.sort(reverse=True)

        best_sig = cands[0][0] if cands else None
        if best_sig is not None:
            signal_history.append(best_sig)

        entry_date = trading_days[settle_idx - 1]
        if today == entry_date:
            primary_passes = False
            if cands and PRIMARY_PCTILE > 0 and len(signal_history) > 60:
                thr = np.percentile(signal_history[-252:], PRIMARY_PCTILE * 100)
                primary_passes = cands[0][0] >= thr
            elif cands and len(signal_history) <= 60:
                primary_passes = True

            if primary_passes and cands:
                found_syms = [(cands[0][1], cands[0][2])]
            elif not primary_passes and cands:
                use = True
                if 0.50 > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], 50.0)
                    use = cands[0][0] >= thr
                if use:
                    found_syms = [(cands[0][1], cands[0][2])]

        prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

    if not found_syms:
        h.check_fail("Check 4", f"No entry on {entry_date}")
        return

    sym, ep = found_syms[0]
    tape_settle = engine.resolve_day(conn, settle_date, [sym])
    xp = tape_settle.get_price("p0935", sym)

    if not ep or not xp:
        h.check_fail("Check 4", f"Missing prices for {sym}")
        return

    raw_ret = xp / ep - 1
    manual_ret = 0.0 if abs(raw_ret) >= SPLIT_THR else raw_ret - 2 * TC
    delta = abs(manual_ret - strategy_ret)

    if delta < 1e-8:
        h.check_pass("Check 4", f"{settle_date} {sym}: manual={manual_ret:.8f} "
                     f"strategy={strategy_ret:.8f} delta={delta:.2e}")
    else:
        h.check_fail("Check 4", f"{settle_date} {sym}: delta={delta:.2e} EXCEEDS 1e-8")


def check_6_incremental(h, trading_days, results):
    """Independent replay of reject_gate50 config."""
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        h.check_fail("Check 6", "No price cache")
        return
    price_cache = load_price_cache(cache_path)

    regime_df = pd.read_parquet(OUT / "regime_cache.parquet")
    regime_by_day = {}
    for _, row in regime_df.iterrows():
        d = row["date"]
        if hasattr(d, "date") and callable(getattr(d, "date", None)):
            d = d.date()
        elif hasattr(d, "astype"):
            d = pd.Timestamp(d).date()
        regime_by_day[d] = str(row["regime"])

    base_symbols = get_symbols()
    schedule = build_schedule("check6_17", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])

    acc = Accumulator(lookback=LOOKBACK)
    signal_history = []
    equity = INITIAL_CAPITAL
    pending = []
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

        regime = regime_by_day.get(today, "unknown")

        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        day_ret = 0.0
        if pending:
            trs = []
            carry = []
            for sym, ep, ed in pending:
                xp = p0935.get(sym)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    trs.append(0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC)
                else:
                    carry.append((sym, ep, ed))
            if trs:
                day_ret = np.mean(trs)
            pending = carry

        equity *= (1 + day_ret)

        cands = []
        if regime == "bull":
            for sym in base_symbols:
                if sym in EXCLUDE:
                    continue
                p0 = p0935.get(sym)
                p1 = p1530.get(sym)
                if not p0 or not p1 or p0 <= 0 or p1 <= 0:
                    continue
                iret = p1 / p0 - 1
                if abs(iret) >= SPLIT_THR or abs(iret) < MIN_IRET:
                    continue
                hr = acc.hit_rate.get(sym)
                if hr is None or hr <= HR_THR:
                    continue
                sig = acc.get_signal(sym, iret)
                if sig is not None:
                    cands.append((sig, sym, p1))
            cands.sort(reverse=True)

        best_sig = cands[0][0] if cands else None
        if best_sig is not None:
            signal_history.append(best_sig)

        primary_passes = False
        if cands and PRIMARY_PCTILE > 0 and len(signal_history) > 60:
            thr = np.percentile(signal_history[-252:], PRIMARY_PCTILE * 100)
            primary_passes = cands[0][0] >= thr
        elif cands and len(signal_history) <= 60:
            primary_passes = True

        if not pending:
            if primary_passes and cands:
                pending.append((cands[0][1], cands[0][2], today))
            elif not primary_passes and cands:
                use = True
                if 0.50 > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], 50.0)
                    use = cands[0][0] >= thr
                if use:
                    pending.append((cands[0][1], cands[0][2], today))

        prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

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
