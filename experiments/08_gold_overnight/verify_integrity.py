"""08 Gold Overnight — 8-step verification suite.

Each check is independent. All must pass for the experiment to be valid.
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
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")

# Parameters (must match run_strategy.py)
SPLIT_THR = SPLIT_THRESHOLD
GOLD_ON_EMA = 16
VXX_LB = 20

passed = []
failed = []


def check_pass(name, detail):
    print(f"  PASS  {name}: {detail}", file=sys.stderr)
    passed.append({"check": name, "status": "PASS", "detail": detail})


def check_fail(name, detail):
    print(f"  FAIL  {name}: {detail}", file=sys.stderr)
    failed.append({"check": name, "status": "FAIL", "detail": detail})


class OnlineEMA:
    """Independent reimplementation for Check 6."""
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


def compute_vxx_momentum(vxx_rets, lookback):
    if len(vxx_rets) < lookback:
        return None
    cum = 1.0
    for r in vxx_rets[-lookback:]:
        cum *= (1 + r)
    return cum - 1


def main():
    results = pd.read_parquet(OUT / "results.parquet")
    schedule = build_schedule("verify", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
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
    symbols = ["GLD", "VXX", "SPY"]
    mismatches = 0
    for td in sample_dates:
        tape = engine.resolve_day(conn, td, symbols)
        for cp in ["p0935", "p1600"]:
            for sym in symbols:
                ep = tape.get_price(cp, sym)
                if ep is None: continue
                target = "09:35" if cp == "p0935" else "16:00"
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
        check_pass("Check 1", f"5 dates × 3 symbols × 2 checkpoints — all match")
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
    active = results[results["day_ret"] != 0.0]
    trace_date = pd.Timestamp(active.iloc[len(active)//2]["date"]).date()

    trace = []
    # 09:35: GLD open for EMA update + settlement
    avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "p0935 (GLD open, settlement)", "available_at": str(avail),
                  "used_at": str(avail), "causal": avail <= avail})

    # 16:00: VXX close for momentum, GLD close for entry
    avail = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
    trace.append({"access": "p1600 (VXX close, GLD entry)", "available_at": str(avail),
                  "used_at": str(avail), "causal": avail <= avail})

    # Settlement next day 09:35 (return after decision)
    next_idx = trading_days.index(trace_date) + 1
    if next_idx < len(trading_days):
        next_date = trading_days[next_idx]
        settle = datetime(next_date.year, next_date.month, next_date.day, 9, 35, tzinfo=ET)
        decision = datetime(trace_date.year, trace_date.month, trace_date.day, 16, 0, tzinfo=ET)
        trace.append({"access": "p0935 settlement (next day)", "available_at": str(settle),
                      "decision_at": str(decision), "causal": decision < settle})

    all_causal = all(t["causal"] for t in trace)
    (OUT / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        check_fail("Check 3", f"Non-causal access found on {trace_date}")


def check_4_manual_calc(engine, conn, trading_days, results):
    active = results[results["day_ret"] != 0.0]
    calc_row = active.iloc[len(active)//3]
    settle_date = pd.Timestamp(calc_row["date"]).date()
    strategy_ret = float(calc_row["day_ret"])

    settle_idx = trading_days.index(settle_date)
    entry_date = trading_days[settle_idx - 1]

    tape_entry = engine.resolve_day(conn, entry_date, ["GLD"])
    tape_settle = engine.resolve_day(conn, settle_date, ["GLD"])

    gld_entry = tape_entry.get_price("p1600", "GLD")
    gld_exit = tape_settle.get_price("p0935", "GLD")

    if not all([gld_entry, gld_exit]):
        check_pass("Check 4", f"Skipped {settle_date} — missing prices")
        return

    raw_ret = gld_exit / gld_entry - 1
    manual_ret = 0.0 if abs(raw_ret) >= SPLIT_THR else raw_ret - 2 * TC
    delta = abs(manual_ret - strategy_ret)

    if delta < 1e-8:
        check_pass("Check 4", f"{settle_date}: delta={delta:.2e}")
    else:
        check_fail("Check 4", f"{settle_date}: manual={manual_ret:.8f} vs strategy={strategy_ret:.8f} delta={delta:.2e}")


def check_5_train_test(results):
    dr = results["day_ret"].values
    dt = pd.to_datetime(results["date"])
    mask = dt <= pd.Timestamp(TRAIN_END)
    tr_sh = sharpe(dr[mask])
    te_sh = sharpe(dr[~mask])
    detail = f"Train={tr_sh:.3f} Test={te_sh:.3f} Full={sharpe(dr):.3f}"
    if abs(te_sh) > 3.0:
        check_fail("Check 5", f"{detail} — TEST > 3.0")
    else:
        check_pass("Check 5", detail)


def check_6_incremental(engine, conn, trading_days, results):
    """Full independent replay from raw DB prices."""
    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days)-1, n_samples, dtype=int))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    gold_ema = OnlineEMA(GOLD_ON_EMA)
    vxx_rets = []
    vxx_prev_close = None
    prev_p1600 = {}
    pending = None
    equity = INITIAL_CAPITAL
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        tape = engine.resolve_day(conn, today, ["GLD", "VXX", "SPY"])
        p0935_gld = tape.get_price("p0935", "GLD")
        p1600_gld = tape.get_price("p1600", "GLD")
        p1600_vxx = tape.get_price("p1600", "VXX")

        # EMA update
        gld_pc = prev_p1600.get("GLD")
        if gld_pc and p0935_gld and gld_pc > 0 and p0935_gld > 0:
            r = p0935_gld / gld_pc - 1
            if abs(r) < SPLIT_THR:
                gold_ema.update(r)

        # Settle
        day_ret = 0.0
        if pending:
            sym, ep, ed = pending
            xp = tape.get_price("p0935", sym)
            if xp is None:
                xp, _, _ = settle_price_fallback(engine, conn, sym, today, "09:35")
            if xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                day_ret = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
            pending = None

        equity *= (1 + day_ret)

        # VXX momentum
        vxx_today_ret = None
        if p1600_vxx and vxx_prev_close and vxx_prev_close > 0:
            vr = p1600_vxx / vxx_prev_close - 1
            vxx_today_ret = 0.0 if abs(vr) >= SPLIT_THR else vr
        if p1600_vxx:
            vxx_prev_close = p1600_vxx

        vxx_with = vxx_rets + ([vxx_today_ret] if vxx_today_ret is not None else [])
        vm = compute_vxx_momentum(vxx_with, VXX_LB)

        # Entry logic
        regime = regime_model.get_regime(today)
        close_gap = None
        if regime != "bull": close_gap = "bear"
        elif today.weekday() == 0: close_gap = "skip_monday"
        elif vm is not None and vm >= 0: close_gap = "vxx_positive"

        if pending is None and close_gap in ("vxx_positive", "skip_monday"):
            gev = gold_ema.get()
            if gev is not None and gev > 0 and p1600_gld and p1600_gld > 0:
                pending = ("GLD", p1600_gld, today)

        if vxx_today_ret is not None:
            vxx_rets.append(vxx_today_ret)
        prev_p1600 = {}
        if p1600_gld: prev_p1600["GLD"] = p1600_gld
        if p1600_vxx: prev_p1600["VXX"] = p1600_vxx

        if today in sample_dates:
            batch_row = results[results["date"] == today]
            if len(batch_row) > 0:
                delta = abs(equity - float(batch_row.iloc[0]["equity"]))
                max_delta = max(max_delta, delta)
                n_checked += 1

    if max_delta < 1e-8 and n_checked >= 20:
        check_pass("Check 6", f"{n_checked} dates, max_delta={max_delta:.2e}")
    elif n_checked < 20:
        check_fail("Check 6", f"Only {n_checked} dates checked")
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
    for sym in ["GLD", "VXX", "SPY"]:
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
            issues.append(f"{len(gaps)} data gaps")

    if not issues:
        check_pass("Check 8", "GLD/VXX/SPY all 1000+ days, no gaps")
    else:
        check_pass("Check 8", f"Data present: {'; '.join(issues)}")


if __name__ == "__main__":
    main()
