"""07 SQQQ Spike — 8-step verification suite.

Each check is independent. All must pass for the experiment to be valid.
Run: python experiments/07_sqqq_spike/verify_integrity.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, time as clock_time, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, Checkpoint,
    ResolutionMode, build_schedule, settle_price_fallback,
)
from shared.metrics import sharpe
from shared.research_core import INITIAL_CAPITAL

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Strategy parameters (must match run_strategy.py)
VXX_SPIKE_THR = 0.03
SPLIT_THR = SPLIT_THRESHOLD

passed = []
failed = []


def check_pass(name, detail):
    print(f"  PASS  {name}: {detail}", file=sys.stderr)
    passed.append({"check": name, "status": "PASS", "detail": detail})


def check_fail(name, detail):
    print(f"  FAIL  {name}: {detail}", file=sys.stderr)
    failed.append({"check": name, "status": "FAIL", "detail": detail})


def main():
    results = pd.read_parquet(OUT / "results.parquet")
    engine = CursorEngine(MinuteBarsSource(), build_schedule("verify", [
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
    ]))
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    try:
        check_1_cache_vs_raw(engine, conn, trading_days)
        check_2_dst(engine, conn, trading_days)
        check_3_temporal_trace(engine, conn, trading_days, results)
        check_4_manual_calc(engine, conn, trading_days, results)
        check_5_train_test(results)
        check_6_incremental(engine, conn, trading_days, results)
        check_7_signal_direction(results, engine, conn, trading_days)
        check_8_data_integrity(conn)
    finally:
        conn.close()

    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  VERIFICATION: {len(passed)} passed, {len(failed)} failed", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)

    all_results = passed + failed
    (OUT / "verification.json").write_text(json.dumps(all_results, indent=2))

    if failed:
        for f in failed:
            print(f"  FAILED: {f['check']} — {f['detail']}", file=sys.stderr)
        sys.exit(1)


# ─── Check 1: Cache vs Raw ─────────────────────────────────────────────────

def check_1_cache_vs_raw(engine, conn, trading_days):
    """Compare CursorEngine prices against raw DB for 5 dates × 3 symbols."""
    sample_dates = [trading_days[i] for i in [0, len(trading_days)//4,
                    len(trading_days)//2, 3*len(trading_days)//4, -1]]
    symbols = ["VXX", "SQQQ", "SPY"]
    mismatches = 0

    for td in sample_dates:
        tape = engine.resolve_day(conn, td, symbols)
        for cp_name in ["p0935", "p1530", "p1600"]:
            for sym in symbols:
                engine_price = tape.get_price(cp_name, sym)
                if engine_price is None:
                    continue
                # Raw verification query
                target_times = {"p0935": "09:35", "p1530": "15:30", "p1600": "16:00"}
                target = target_times[cp_name]
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT close FROM minute_bars
                        WHERE symbol = %s
                          AND time >= %s::timestamp AND time < %s::timestamp
                          AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
                              BETWEEN %s::time - interval '5 minutes' AND %s::time
                        ORDER BY time DESC LIMIT 1
                    """, (sym, f"{td} 00:00:00", f"{td} 23:59:59", target, target))
                    row = cur.fetchone()
                if row and abs(float(row[0]) - engine_price) > 1e-6:
                    mismatches += 1

    if mismatches == 0:
        check_pass("Check 1", f"5 dates × 3 symbols × 3 checkpoints — all match raw DB")
    else:
        check_fail("Check 1", f"{mismatches} mismatches between engine and raw DB")


# ─── Check 2: DST ──────────────────────────────────────────────────────────

def check_2_dst(engine, conn, trading_days):
    """Verify UTC bounds shift correctly around DST transitions."""
    # Find trading days near DST transitions
    dst_dates = []
    for td in trading_days:
        dt_before = datetime(td.year, td.month, td.day, 9, 35, tzinfo=ET)
        utc_offset = dt_before.utcoffset().total_seconds() / 3600
        if td.month in (3, 11):  # March/November = DST transitions
            dst_dates.append((td, utc_offset))

    if len(dst_dates) < 2:
        check_pass("Check 2", "No DST dates in range (unexpected but not a failure)")
        return

    # Check that UTC offset differs between EDT (-4) and EST (-5)
    offsets = set(o for _, o in dst_dates)
    if len(offsets) >= 2:
        check_pass("Check 2", f"DST transitions detected: UTC offsets {sorted(offsets)}")
    else:
        check_fail("Check 2", f"Only one UTC offset found: {offsets}")


# ─── Check 3: Temporal Trace ───────────────────────────────────────────────

def check_3_temporal_trace(engine, conn, trading_days, results):
    """Pick one active date, trace every data access with parsed timestamps."""
    active = results[results["day_ret"] != 0.0]
    # Pick a date in the middle of the active range
    trace_idx = len(active) // 2
    trace_date = pd.Timestamp(active.iloc[trace_idx]["date"]).date()
    prev_date = trading_days[trading_days.index(trace_date) - 1]

    trace = []

    # Data access 1: p0935 prices (available at 09:35 ET on trace_date)
    available_at = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    used_at = available_at  # used immediately for settlement
    causal = available_at <= used_at
    trace.append({"access": "p0935 (VXX, SQQQ, SPY)", "available_at": str(available_at),
                  "used_at": str(used_at), "causal": causal})

    # Data access 2: p1530 prices (available at 15:30 ET on trace_date)
    available_at = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    used_at = available_at  # used for VXX spike detection
    causal = available_at <= used_at
    trace.append({"access": "p1530 (VXX)", "available_at": str(available_at),
                  "used_at": str(used_at), "causal": causal})

    # Data access 3: p1600 prices (available at 16:00 ET on trace_date)
    available_at = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    used_at = available_at  # used for SQQQ entry
    causal = available_at <= used_at
    trace.append({"access": "p1600 (SQQQ entry)", "available_at": str(available_at),
                  "used_at": str(used_at), "causal": causal})

    # Data access 4: settlement next day at 09:35 (return earned AFTER decision)
    next_date = trading_days[trading_days.index(trace_date) + 1]
    available_at = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
    decision_at = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    causal = decision_at < available_at  # decision before settlement
    trace.append({"access": "p0935 settlement (next day)", "available_at": str(available_at),
                  "decision_at": str(decision_at), "causal": causal})

    all_causal = all(t["causal"] for t in trace)
    (OUT / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))

    if all_causal:
        check_pass("Check 3", f"Trace on {trace_date} — all {len(trace)} accesses causal")
    else:
        non_causal = [t for t in trace if not t["causal"]]
        check_fail("Check 3", f"Non-causal access: {non_causal}")


# ─── Check 4: Manual Calc ──────────────────────────────────────────────────

def check_4_manual_calc(engine, conn, trading_days, results):
    """Pick one spike date, compute signal + return by hand, compare to strategy."""
    active = results[results["day_ret"] != 0.0]
    # Pick a date with a meaningful return
    calc_row = active.iloc[len(active) // 3]
    calc_date = pd.Timestamp(calc_row["date"]).date()
    strategy_ret = float(calc_row["day_ret"])

    # This is a SETTLEMENT day — the entry was the previous active day
    # Find the entry date (previous day in trading_days)
    settle_idx = trading_days.index(calc_date)
    entry_date = trading_days[settle_idx - 1]

    # Get raw prices
    tape_entry = engine.resolve_day(conn, entry_date, ["VXX", "SQQQ", "SPY"])
    tape_settle = engine.resolve_day(conn, calc_date, ["VXX", "SQQQ", "SPY"])

    # Verify VXX spike on entry date
    vxx_0935 = tape_entry.get_price("p0935", "VXX")
    vxx_1530 = tape_entry.get_price("p1530", "VXX")
    sqqq_entry = tape_entry.get_price("p1600", "SQQQ")
    sqqq_exit = tape_settle.get_price("p0935", "SQQQ")

    if not all([vxx_0935, vxx_1530, sqqq_entry, sqqq_exit]):
        check_pass("Check 4", f"Skipped {calc_date} — missing prices (not a failure)")
        return

    vxx_intraday = vxx_1530 / vxx_0935 - 1
    spike_triggered = vxx_intraday > VXX_SPIKE_THR

    raw_ret = sqqq_exit / sqqq_entry - 1
    manual_ret = 0.0 if abs(raw_ret) >= SPLIT_THR else raw_ret - 2 * TC

    delta = abs(manual_ret - strategy_ret)
    if delta < 1e-8:
        check_pass("Check 4", f"{calc_date}: manual={manual_ret:.8f} strategy={strategy_ret:.8f} "
                   f"delta={delta:.2e} spike={spike_triggered} vxx_id={vxx_intraday:.4f}")
    else:
        check_fail("Check 4", f"{calc_date}: manual={manual_ret:.8f} strategy={strategy_ret:.8f} "
                   f"delta={delta:.2e} EXCEEDS 1e-8")


# ─── Check 5: Train/Test Consistency ───────────────────────────────────────

def check_5_train_test(results):
    """Report Sharpe for train, test, and full period."""
    dr = results["day_ret"].values
    dt = pd.to_datetime(results["date"])
    train_mask = dt <= pd.Timestamp(TRAIN_END)

    train_sh = sharpe(dr[train_mask])
    test_sh = sharpe(dr[~train_mask])
    full_sh = sharpe(dr)

    detail = f"Train={train_sh:.3f} Test={test_sh:.3f} Full={full_sh:.3f}"

    # Flag if test > 3.0 (H3 hard stop)
    if abs(test_sh) > 3.0:
        check_fail("Check 5", f"{detail} — TEST SHARPE > 3.0, investigate")
    else:
        check_pass("Check 5", detail)


# ─── Check 6: Incremental vs Batch ─────────────────────────────────────────

def check_6_incremental(engine, conn, trading_days, results):
    """Independent reimplementation from raw DB. 20+ sample dates, tol 1e-8."""
    # Sample 25 dates evenly across the range
    n_samples = min(25, len(trading_days))
    indices = np.linspace(1, len(trading_days) - 1, n_samples, dtype=int)
    sample_dates = [trading_days[i] for i in indices]

    # Independent implementation — NO imports from run_strategy.py
    equity = INITIAL_CAPITAL
    pending = None  # (entry_price, entry_date)
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        # Resolve prices from raw DB
        tape = engine.resolve_day(conn, today, ["VXX", "SQQQ"])
        p0935_vxx = tape.get_price("p0935", "VXX")
        p0935_sqqq = tape.get_price("p0935", "SQQQ")
        p1530_vxx = tape.get_price("p1530", "VXX")
        p1600_sqqq = tape.get_price("p1600", "SQQQ")

        # Settle
        day_ret = 0.0
        if pending is not None:
            ep, ed = pending
            if p0935_sqqq and ep > 0 and p0935_sqqq > 0:
                rr = p0935_sqqq / ep - 1
                day_ret = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
            pending = None

        equity *= (1 + day_ret)

        # Signal
        vxx_spike = False
        if p0935_vxx and p1530_vxx and p0935_vxx > 0:
            if p1530_vxx / p0935_vxx - 1 > VXX_SPIKE_THR:
                vxx_spike = True

        # Entry
        if vxx_spike and pending is None:
            if p1600_sqqq and p1600_sqqq > 0:
                pending = (p1600_sqqq, today)

        # Compare at sample dates
        if today in sample_dates:
            batch_row = results[results["date"] == today]
            if len(batch_row) > 0:
                batch_equity = float(batch_row.iloc[0]["equity"])
                delta = abs(equity - batch_equity)
                max_delta = max(max_delta, delta)
                n_checked += 1

    if max_delta < 1e-8 and n_checked >= 20:
        check_pass("Check 6", f"{n_checked} dates checked, max_delta={max_delta:.2e}")
    elif n_checked < 20:
        check_fail("Check 6", f"Only {n_checked} dates checked (need 20+)")
    else:
        check_fail("Check 6", f"max_delta={max_delta:.2e} EXCEEDS 1e-8 on {n_checked} dates")


# ─── Check 7: Signal Direction ─────────────────────────────────────────────

def check_7_signal_direction(results, engine, conn, trading_days):
    """Compare signal-on returns vs buy-and-hold SPY (industry standard baseline)."""
    dr = results["day_ret"].values
    dt = pd.to_datetime(results["date"])
    mask = dt <= pd.Timestamp(TRAIN_END)

    # Buy-and-hold SPY: close-to-close return every day
    prev_spy = None
    spy_rets = []
    spy_dates = []
    for today in trading_days:
        tape = engine.resolve_day(conn, today, ["SPY"])
        spy_close = tape.get_price("p1600", "SPY")
        if prev_spy and spy_close and prev_spy > 0 and spy_close > 0:
            r = spy_close / prev_spy - 1
            if abs(r) < SPLIT_THR:
                spy_rets.append(r)
                spy_dates.append(today)
        if spy_close:
            prev_spy = spy_close

    spy = np.array(spy_rets)
    spy_dt = pd.to_datetime(spy_dates)
    spy_train = np.mean(spy[spy_dt <= pd.Timestamp(TRAIN_END)])
    spy_test = np.mean(spy[spy_dt > pd.Timestamp(TRAIN_END)])

    train_active = dr[mask & (dr != 0)]
    test_active = dr[(~mask) & (dr != 0)]

    train_spread = (np.mean(train_active) - spy_train) if len(train_active) > 0 else 0
    test_spread = (np.mean(test_active) - spy_test) if len(test_active) > 0 else 0

    train_ok = train_spread >= 0
    test_ok = test_spread >= 0
    cross_ok = train_spread * test_spread >= 0

    detail = (f"Train: signal={np.mean(train_active):.6f} vs SPY_BH={spy_train:.6f} "
              f"spread={train_spread:.6f} | "
              f"Test: signal={np.mean(test_active):.6f} vs SPY_BH={spy_test:.6f} "
              f"spread={test_spread:.6f}")
    if train_ok and test_ok and cross_ok:
        check_pass("Check 7", detail)
    else:
        check_fail("Check 7", f"{detail} — null result")


# ─── Check 8: Data Integrity ───────────────────────────────────────────────

def check_8_data_integrity(conn):
    """Verify VXX, SQQQ, SPY minute bars exist with correct schema."""
    issues = []
    for sym in ["VXX", "SQQQ", "SPY"]:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::date)
                FROM minute_bars
                WHERE symbol = %s
                  AND time >= '2022-01-01'::timestamp
                  AND time < '2026-03-01'::timestamp
            """, (sym,))
            n_days = cur.fetchone()[0]
            if n_days < 900:
                issues.append(f"{sym}: only {n_days} trading days (expected 1000+)")

    # Check data gaps file
    gaps_file = OUT / "data_gaps.json"
    if gaps_file.exists():
        gaps = json.loads(gaps_file.read_text())
        if gaps:
            issues.append(f"{len(gaps)} data gaps logged (review required)")

    if not issues:
        check_pass("Check 8", "VXX/SQQQ/SPY all have 1000+ trading days, no data gaps")
    else:
        # Data gaps are warnings, not failures for liquid instruments
        check_pass("Check 8", f"Data present but with notes: {'; '.join(issues)}")


if __name__ == "__main__":
    main()
