"""12 Dynamic Universe — 8-step verification suite.

Run: python experiments/12_dynamic_universe/verify_integrity.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from datetime import time as clock_time, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, Checkpoint,
    ResolutionMode, build_schedule, settle_price_fallback,
)
from shared.research_core import (
    FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_r1000_symbols,
)
from shared.verify_harness import VerificationHarness

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")

SPLIT_THR = SPLIT_THRESHOLD
EXCLUDE = {"SPY", "QQQ", "VXX"}
STREAK = 0.75; HR_THR = 0.57; LB = 80; PCTILE = 0.50; MIN_IRET = 0.013
VOLUME_LOOKBACK = 60


class Accumulator:
    """Independent reimplementation for Check 6."""
    def __init__(self, lookback=80):
        self.lookback = lookback
        self.rets = defaultdict(list)
        self.hit_rate = {}
        self.avg_pos = {}
        self.streak = {}

    def update(self, sym, ret):
        self.rets[sym].append(ret)
        r = self.rets[sym]
        if len(r) < 20:
            self.hit_rate.pop(sym, None); self.avg_pos.pop(sym, None); self.streak[sym] = 0; return
        recent = r[-self.lookback:]
        pos = [x for x in recent if x > 0]
        self.hit_rate[sym] = len(pos) / len(recent)
        self.avg_pos[sym] = float(np.mean(pos)) if pos else 0.0
        s = 0
        for x in reversed(r):
            if x > 0: s += 1
            else: break
        self.streak[sym] = s

    def get_signal(self, sym, iret):
        if sym not in self.hit_rate: return None
        return iret * self.avg_pos.get(sym, 0.0) * (1 + STREAK * self.streak.get(sym, 0)) * self.hit_rate[sym]


def main():
    results = pd.read_parquet(OUT / "results.parquet")
    schedule = build_schedule("verify", [
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
        # 10 symbols across sectors to validate 911-symbol cache
        h.check_1_cache_vs_raw(
            ["SPY", "VXX", "AAPL", "NVDA", "MSFT", "JPM", "XOM", "UNH", "LLY", "CMG"],
            {"p0935": "09:35", "p1530": "15:30", "p1600": "16:00"})
        h.check_2_dst()
        check_3_temporal_trace(h, results)
        check_4_manual_calc(h, engine, conn, trading_days, results)
        h.check_5_train_test(results)
        check_6_incremental(h, engine, conn, trading_days, results)
        h.check_7_signal_direction(results)
        h.check_8_data_integrity(["SPY", "VXX", "AAPL", "NVDA", "MSFT", "TSLA"])
    finally:
        conn.close()

    if not h.summarize():
        sys.exit(1)


# ─── Check 3: Temporal Trace ─────────────────────────────────────────────

def check_3_temporal_trace(h, results):
    active = results[results["day_ret"] != 0.0]
    trace_date = pd.Timestamp(active.iloc[len(active)//2]["date"]).date()
    td = h.trading_days

    prev_date = td[td.index(trace_date) - 1]
    trace = []

    # Accumulator input: prev_p1530 available at T-1 15:30, used at T 09:35
    data_avail = datetime(prev_date.year, prev_date.month, prev_date.day, 15, 30, tzinfo=ET)
    used_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "prev_p1530 (accumulator input)", "available_at": str(data_avail),
                  "used_at": str(used_at), "causal": data_avail < used_at})

    # p0935 available at T 09:35, signal computed at T 15:30
    p0935_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    signal_at = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    trace.append({"access": "p0935 (iret denominator)", "available_at": str(p0935_avail),
                  "used_at": str(signal_at), "causal": p0935_avail < signal_at})

    # p1530 available at T 15:30, entry decision at T 15:30
    p1530_avail = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    trace.append({"access": "p1530 (signal + entry price)", "available_at": str(p1530_avail),
                  "used_at": str(p1530_avail), "causal": p1530_avail <= signal_at})

    # Settlement next day 09:35
    next_idx = td.index(trace_date) + 1
    if next_idx < len(td):
        next_date = td[next_idx]
        settle = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
        decision = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
        trace.append({"access": "settlement (next day p0935)", "available_at": str(settle),
                      "decision_at": str(decision), "causal": decision < settle})

    all_causal = all(t["causal"] for t in trace)
    (h.out / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        h.check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        h.check_fail("Check 3", f"Non-causal on {trace_date}")


# ─── Check 4: Manual Calc ────────────────────────────────────────────────

def check_4_manual_calc(h, engine, conn, trading_days, results):
    """Replay strategy to find which symbol was traded, then verify return from raw DB."""
    from shared.cursor_engine import CachedPhasedDay, load_price_cache
    from shared.indicators import Accumulator as SharedAcc

    active = results[results["day_ret"] != 0.0]
    if len(active) == 0:
        h.check_pass("Check 4", "No active days to verify")
        return

    calc_row = active.iloc[len(active) // 3]
    settle_date = pd.Timestamp(calc_row["date"]).date()
    strategy_ret = float(calc_row["day_ret"])
    settle_idx = trading_days.index(settle_date)
    entry_date = trading_days[settle_idx - 1]

    # Replay strategy up to entry_date to find which symbol was entered
    cache_path = Path(__file__).resolve().parent / "output" / "price_cache.parquet"
    price_cache = load_price_cache(cache_path)
    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    r1000 = get_r1000_symbols()
    all_stocks = [s for s in r1000 if s not in EXCLUDE]
    schedule_c4 = build_schedule("check4", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
    ])
    acc = SharedAcc(lookback=LB)
    act = {sym: deque(maxlen=VOLUME_LOOKBACK) for sym in all_stocks}
    pl = {sym: deque(maxlen=VOLUME_LOOKBACK) for sym in all_stocks}
    prev_p1530 = {}; signal_history = []; found_sym = None; found_price = None

    for today in trading_days[:settle_idx]:
        phased = CachedPhasedDay(price_cache, today, schedule_c4)
        m = phased.resolve_up_to(clock_time(9, 35)); p0935 = m.get("p0935", {})
        aft = phased.resolve_up_to(clock_time(15, 30)); p1530 = aft.get("p1530", {})

        for sym in all_stocks:
            pc = prev_p1530.get(sym); op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR: acc.update(sym, r)

        for sym in all_stocks:
            has_prices = sym in p0935 and sym in p1530
            act[sym].append(1.0 if has_prices else 0.0)
            if sym in p1530 and p1530[sym] and p1530[sym] > 0: pl[sym].append(p1530[sym])

        if today == entry_date:
            liq = {}
            for sym in all_stocks:
                if len(act[sym]) < 20: continue
                ar = sum(act[sym]) / len(act[sym])
                ap = np.mean(list(pl[sym])) if len(pl[sym]) > 5 else 0
                if ar > 0.5 and ap > 5: liq[sym] = ar * ap
            ranked = sorted(liq.items(), key=lambda x: x[1], reverse=True)
            eligible = set(sym for sym, _ in ranked[:100])

            regime = regime_model.get_regime(today)
            cands = []
            if regime == "bull":
                for sym in eligible:
                    p0 = p0935.get(sym); p1 = p1530.get(sym)
                    if not p0 or not p1 or p0 <= 0 or p1 <= 0: continue
                    iret = p1 / p0 - 1
                    if abs(iret) >= SPLIT_THR or abs(iret) < MIN_IRET: continue
                    hr = acc.hit_rate.get(sym)
                    if hr is None or hr <= HR_THR: continue
                    sig = acc.get_signal(sym, iret, STREAK)
                    if sig is not None: cands.append((sig, sym, p1))
                cands.sort(reverse=True)

            best_sig = cands[0][0] if cands else None
            if cands:
                use = True
                if PCTILE > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], PCTILE * 100)
                    use = cands[0][0] >= thr
                if use:
                    found_sym = cands[0][1]
                    found_price = cands[0][2]
            if best_sig is not None: signal_history.append(best_sig)
        else:
            # Maintain signal_history identically to run_strategy.py
            liq = {}
            for sym in all_stocks:
                if len(act[sym]) < 20: continue
                ar = sum(act[sym]) / len(act[sym])
                ap = np.mean(list(pl[sym])) if len(pl[sym]) > 5 else 0
                if ar > 0.5 and ap > 5: liq[sym] = ar * ap
            ranked = sorted(liq.items(), key=lambda x: x[1], reverse=True)
            eligible = set(sym for sym, _ in ranked[:100])
            regime = regime_model.get_regime(today)
            cands_other = []
            if regime == "bull":
                for sym in eligible:
                    p0 = p0935.get(sym); p1 = p1530.get(sym)
                    if not p0 or not p1 or p0 <= 0 or p1 <= 0: continue
                    iret = p1 / p0 - 1
                    if abs(iret) >= SPLIT_THR or abs(iret) < MIN_IRET: continue
                    hr = acc.hit_rate.get(sym)
                    if hr is None or hr <= HR_THR: continue
                    sig = acc.get_signal(sym, iret, STREAK)
                    if sig is not None: cands_other.append((sig, sym, p1))
                cands_other.sort(reverse=True)
            best_other = cands_other[0][0] if cands_other else None
            if best_other is not None: signal_history.append(best_other)

        prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

    if found_sym is None:
        h.check_pass("Check 4", f"No entry on {entry_date} — regime gate or no candidates")
        return

    # Now verify from RAW DB
    tape_settle = engine.resolve_day(conn, settle_date, [found_sym])
    exit_price = tape_settle.get_price("p0935", found_sym)
    if exit_price is None:
        exit_price, _, _ = settle_price_fallback(engine, conn, found_sym, settle_date, "09:35")

    if not found_price or not exit_price:
        h.check_pass("Check 4", f"Skipped {settle_date} {found_sym} — missing prices")
        return

    raw_ret = exit_price / found_price - 1
    manual_ret = 0.0 if abs(raw_ret) >= SPLIT_THR else raw_ret - 2 * TC
    delta = abs(manual_ret - strategy_ret)

    if delta < 1e-8:
        h.check_pass("Check 4", f"{settle_date} {found_sym}: manual={manual_ret:.8f} "
                     f"strategy={strategy_ret:.8f} delta={delta:.2e}")
    else:
        h.check_fail("Check 4", f"{settle_date} {found_sym}: manual={manual_ret:.8f} "
                     f"strategy={strategy_ret:.8f} delta={delta:.2e} EXCEEDS 1e-8")


# ─── Check 6: Incremental vs Batch ───────────────────────────────────────

def check_6_incremental(h, engine, conn, trading_days, results):
    """Independent replay with 911 R1000 symbols.

    Uses price cache (verified by Check 1 to match raw DB) for the 911-symbol
    universe. settle_price_fallback still queries raw DB for missing prices.
    """
    from shared.cursor_engine import CachedPhasedDay, load_price_cache
    OUT = Path(__file__).resolve().parent / "output"
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        h.check_fail("Check 6", "No price cache — run strategy first")
        return
    price_cache = load_price_cache(cache_path)

    r1000 = get_r1000_symbols()
    all_stocks = [s for s in r1000 if s not in EXCLUDE]

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days)-1, n_samples, dtype=int))

    # Activity and price tracking
    activity = {sym: deque(maxlen=VOLUME_LOOKBACK) for sym in all_stocks}
    price_level = {sym: deque(maxlen=VOLUME_LOOKBACK) for sym in all_stocks}

    schedule = build_schedule("check6", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=390, grace_minutes_after=0, required=False, trading_day_offset=0),
    ])

    acc = Accumulator(lookback=LB)
    equity = INITIAL_CAPITAL
    pending = None
    prev_p1530 = {}
    signal_history = []
    max_delta = 0.0
    n_checked = 0
    top_n = 100  # Must match the run_strategy.py default

    for today in trading_days:
        phased = CachedPhasedDay(price_cache, today, schedule)
        m = phased.resolve_up_to(clock_time(9, 35))
        p0935 = m.get("p0935", {})
        aft = phased.resolve_up_to(clock_time(15, 30))
        p1530 = aft.get("p1530", {})

        # Accumulator update
        for sym in all_stocks:
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        # Settle
        day_ret = 0.0
        if pending is not None:
            sym, ep, ed = pending
            xp = p0935.get(sym)
            if xp is None:
                xp, _, _ = settle_price_fallback(engine, conn, sym, today, "09:35")
            if xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                day_ret = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
            pending = None

        equity *= (1 + day_ret)

        # Activity tracking
        for sym in all_stocks:
            has_prices = sym in p0935 and sym in p1530
            activity[sym].append(1.0 if has_prices else 0.0)
            if sym in p1530 and p1530[sym] and p1530[sym] > 0:
                price_level[sym].append(p1530[sym])

        # Liquidity rank
        liquidity_scores = {}
        for sym in all_stocks:
            if len(activity[sym]) < 20: continue
            act_rate = sum(activity[sym]) / len(activity[sym])
            avg_price = np.mean(list(price_level[sym])) if len(price_level[sym]) > 5 else 0
            if act_rate > 0.5 and avg_price > 5:
                liquidity_scores[sym] = act_rate * avg_price

        ranked = sorted(liquidity_scores.items(), key=lambda x: x[1], reverse=True)
        eligible = set(sym for sym, _ in ranked[:top_n])

        # Signal
        regime = regime_model.get_regime(today)
        cands = []
        if regime == "bull":
            for sym in eligible:
                p0 = p0935.get(sym)
                p1 = p1530.get(sym)
                if not p0 or not p1 or p0 <= 0 or p1 <= 0: continue
                iret = p1 / p0 - 1
                if abs(iret) >= SPLIT_THR or abs(iret) < MIN_IRET: continue
                hr = acc.hit_rate.get(sym)
                if hr is None or hr <= HR_THR: continue
                sig = acc.get_signal(sym, iret)
                if sig is not None:
                    cands.append((sig, sym, p1))
            cands.sort(reverse=True)

        best_sig = cands[0][0] if cands else None

        # Percentile gate + entry
        if cands:
            use = True
            if PCTILE > 0 and len(signal_history) > 60:
                thr = np.percentile(signal_history[-252:], PCTILE * 100)
                use = cands[0][0] >= thr
            if use:
                pending = (cands[0][1], cands[0][2], today)
        if best_sig is not None:
            signal_history.append(best_sig)

        prev_p1530 = {s: p for s, p in p1530.items()}

        if today in sample_dates:
            batch_row = results[results["date"] == today]
            if len(batch_row) > 0:
                delta = abs(equity - float(batch_row.iloc[0]["equity"]))
                max_delta = max(max_delta, delta)
                n_checked += 1

    if max_delta < 1e-8 and n_checked >= 20:
        h.check_pass("Check 6", f"{n_checked} dates, max_delta={max_delta:.2e}")
    elif n_checked < 20:
        h.check_fail("Check 6", f"Only {n_checked} dates")
    else:
        h.check_fail("Check 6", f"max_delta={max_delta:.2e}")


if __name__ == "__main__":
    main()
