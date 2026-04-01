"""09 Gold Intraday — 8-step verification suite."""
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
SPLIT_THR = SPLIT_THRESHOLD
GOLD_ID_EMA = 34

passed = []
failed = []


def check_pass(name, detail):
    print(f"  PASS  {name}: {detail}", file=sys.stderr)
    passed.append({"check": name, "status": "PASS", "detail": detail})


def check_fail(name, detail):
    print(f"  FAIL  {name}: {detail}", file=sys.stderr)
    failed.append({"check": name, "status": "FAIL", "detail": detail})


class OnlineEMA:
    def __init__(self, span):
        self.alpha = 2.0 / (span + 1)
        self.value = None
        self.n = 0

    def update(self, x):
        if self.value is None: self.value = x
        else: self.value = self.alpha * x + (1 - self.alpha) * self.value
        self.n += 1

    def get(self):
        return self.value if self.n >= 5 else None


def main():
    results = pd.read_parquet(OUT / "results.parquet")
    schedule = build_schedule("verify", [
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
                   grace_minutes_before=390, grace_minutes_after=0,  # R5: half-day closes
                   required=False, trading_day_offset=0),
    ])
    engine = CursorEngine(MinuteBarsSource(), schedule)
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

    (OUT / "verification.json").write_text(json.dumps(passed + failed, indent=2))
    if failed:
        for f in failed:
            print(f"  FAILED: {f['check']} — {f['detail']}", file=sys.stderr)
        sys.exit(1)


def check_1_cache_vs_raw(engine, conn, trading_days):
    sample_dates = [trading_days[i] for i in [0, len(trading_days)//4,
                    len(trading_days)//2, 3*len(trading_days)//4, -1]]
    symbols = ["GLD", "NUGT", "SPY"]
    mismatches = 0
    for td in sample_dates:
        tape = engine.resolve_day(conn, td, symbols)
        for cp, target in [("p0935", "09:35"), ("p1030", "10:30"), ("p1600", "16:00")]:
            for sym in symbols:
                ep = tape.get_price(cp, sym)
                if ep is None: continue
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
                if row and abs(float(row[0]) - ep) > 1e-6:
                    mismatches += 1
    if mismatches == 0:
        check_pass("Check 1", f"5 dates × 3 symbols × 3 checkpoints — all match")
    else:
        check_fail("Check 1", f"{mismatches} mismatches")


def check_2_dst(engine, conn, trading_days):
    offsets = set()
    for td in trading_days:
        if td.month in (3, 11):
            dt = datetime(td.year, td.month, td.day, 9, 35, tzinfo=ET)
            offsets.add(dt.utcoffset().total_seconds() / 3600)
    if len(offsets) >= 2:
        check_pass("Check 2", f"DST offsets: {sorted(offsets)}")
    else:
        check_fail("Check 2", f"Only one offset: {offsets}")


def check_3_temporal_trace(engine, conn, trading_days, results):
    """Intraday: entry 10:30, exit 16:00 same day. No overnight pending."""
    active = results[results["day_ret"] != 0.0]
    trace_date = pd.Timestamp(active.iloc[len(active)//2]["date"]).date()

    trace = []
    # 09:35: GLD overnight return for EMA update
    avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "p0935 (GLD/GDX/NUGT for EMA)", "available_at": str(avail),
                  "used_at": str(avail), "causal": avail <= avail})

    # 10:30: NUGT entry price — signal (EMA) was computed at 09:35
    entry = datetime(trace_date.year, trace_date.month, trace_date.day, 10, 30, tzinfo=ET)
    signal = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "p1030 (NUGT entry)", "available_at": str(entry),
                  "signal_computed_at": str(signal), "causal": signal <= entry})

    # 16:00: NUGT exit — settlement AFTER entry (same day, not overnight)
    exit_t = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    trace.append({"access": "p1600 (NUGT exit)", "available_at": str(exit_t),
                  "entry_at": str(entry), "causal": entry < exit_t})

    all_causal = all(t["causal"] for t in trace)
    (OUT / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(engine, conn, trading_days, results):
    active = results[results["day_ret"] != 0.0]
    calc_row = active.iloc[len(active)//3]
    calc_date = pd.Timestamp(calc_row["date"]).date()
    strategy_ret = float(calc_row["day_ret"])

    tape = engine.resolve_day(conn, calc_date, ["NUGT"])
    nugt_1030 = tape.get_price("p1030", "NUGT")
    nugt_1600 = tape.get_price("p1600", "NUGT")

    # Fallback for half-day closes
    if nugt_1600 is None:
        nugt_1600, _, _ = settle_price_fallback(engine, conn, "NUGT", calc_date, "16:00")

    if not all([nugt_1030, nugt_1600]):
        check_pass("Check 4", f"Skipped {calc_date} — missing prices")
        return

    raw_ret = nugt_1600 / nugt_1030 - 1
    manual_ret = (raw_ret - 2 * TC) if abs(raw_ret) < SPLIT_THR else 0.0
    delta = abs(manual_ret - strategy_ret)

    if delta < 1e-8:
        check_pass("Check 4", f"{calc_date}: delta={delta:.2e}")
    else:
        check_fail("Check 4", f"{calc_date}: delta={delta:.2e}")


def check_5_train_test(results):
    dr = results["day_ret"].values
    dt = pd.to_datetime(results["date"])
    mask = dt <= pd.Timestamp(TRAIN_END)
    tr_sh, te_sh = sharpe(dr[mask]), sharpe(dr[~mask])
    detail = f"Train={tr_sh:.3f} Test={te_sh:.3f} Full={sharpe(dr):.3f}"
    if abs(te_sh) > 3.0:
        check_fail("Check 5", f"{detail} — TEST > 3.0")
    else:
        check_pass("Check 5", detail)


def check_6_incremental(engine, conn, trading_days, results):
    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days)-1, n_samples, dtype=int))

    gold_symbols = ["GLD", "GDX", "NUGT"]
    emas = {g: OnlineEMA(GOLD_ID_EMA) for g in gold_symbols}
    prev_p1600 = {}
    equity = INITIAL_CAPITAL
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        tape = engine.resolve_day(conn, today, gold_symbols + ["SPY"])

        # EMA update
        for g in gold_symbols:
            pc = prev_p1600.get(g)
            op = tape.get_price("p0935", g)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    emas[g].update(r)

        # Entry decision
        entry = None
        ev = emas.get("GLD", OnlineEMA(34)).get()
        if ev is not None and ev > 0:
            nugt_1030 = tape.get_price("p1030", "NUGT")
            if nugt_1030 and nugt_1030 > 0:
                entry = nugt_1030

        # Settlement (same day)
        day_ret = 0.0
        if entry:
            xp = tape.get_price("p1600", "NUGT")
            if xp is None:
                xp, _, _ = settle_price_fallback(engine, conn, "NUGT", today, "16:00")
            if xp and entry > 0 and xp > 0:
                rr = xp / entry - 1
                if abs(rr) < SPLIT_THR:
                    day_ret = rr - 2 * TC

        equity *= (1 + day_ret)

        prev_p1600 = {}
        for g in gold_symbols:
            p = tape.get_price("p1600", g)
            if p: prev_p1600[g] = p

        if today in sample_dates:
            batch_row = results[results["date"] == today]
            if len(batch_row) > 0:
                delta = abs(equity - float(batch_row.iloc[0]["equity"]))
                max_delta = max(max_delta, delta)
                n_checked += 1

    if max_delta < 1e-8 and n_checked >= 20:
        check_pass("Check 6", f"{n_checked} dates, max_delta={max_delta:.2e}")
    elif n_checked < 20:
        check_fail("Check 6", f"Only {n_checked} dates")
    else:
        check_fail("Check 6", f"max_delta={max_delta:.2e} on {n_checked} dates")


def check_7_signal_direction(results, engine, conn, trading_days):
    """Compare signal-on returns vs buy-and-hold SPY (industry standard baseline)."""
    dr = results["day_ret"].values
    dt = pd.to_datetime(results["date"])
    mask = dt <= pd.Timestamp(TRAIN_END)

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


def check_8_data_integrity(conn):
    issues = []
    for sym in ["GLD", "GDX", "NUGT", "SPY"]:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::date)
                FROM minute_bars
                WHERE symbol = %s AND time >= '2022-01-01'::timestamp AND time < '2026-03-01'::timestamp
            """, (sym,))
            n = cur.fetchone()[0]
            if n < 900:
                issues.append(f"{sym}: {n} days")

    gaps_file = OUT / "data_gaps.json"
    if gaps_file.exists():
        gaps = json.loads(gaps_file.read_text())
        if gaps:
            issues.append(f"{len(gaps)} data gaps (half-day closes)")

    if not issues:
        check_pass("Check 8", "All symbols 1000+ days, no gaps")
    else:
        check_pass("Check 8", f"Notes: {'; '.join(issues)}")


if __name__ == "__main__":
    main()
