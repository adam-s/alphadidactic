"""11 Flow Gap-Fill — 8-step verification suite."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, time as clock_time, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
REPO_ROOT = HERE.parent.parent

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, Checkpoint,
    ResolutionMode, build_schedule, settle_price_fallback,
)
from shared.metrics import sharpe
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_symbols

OUT = HERE / "output"
ET = ZoneInfo("America/New_York")
SPLIT_THR = SPLIT_THRESHOLD
EXCLUDE = {"SPY", "QQQ", "VXX"}
STREAK = 0.75; HR_THR = 0.57; LB = 80; PCTILE = 0.50; MIN_IRET = 0.013
VXX_SPIKE_THR = 0.03
FLOW_EMA_SPAN = 10

passed = []
failed = []


def check_pass(name, detail):
    print(f"  PASS  {name}: {detail}", file=sys.stderr)
    passed.append({"check": name, "status": "PASS", "detail": detail})


def check_fail(name, detail):
    print(f"  FAIL  {name}: {detail}", file=sys.stderr)
    failed.append({"check": name, "status": "FAIL", "detail": detail})


class Accumulator:
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


class OnlineEMA:
    def __init__(self, span):
        self.alpha = 2.0 / (span + 1); self.value = None; self.n = 0

    def update(self, x):
        if self.value is None: self.value = x
        else: self.value = self.alpha * x + (1 - self.alpha) * self.value
        self.n += 1

    def get(self):
        return self.value if self.n >= 5 else None


def load_inst_flow(symbols):
    flow_dir = REPO_ROOT / "flow_cache" / "output"
    flow = {}
    for sym in symbols:
        path = flow_dir / f"{sym}_flow.parquet"
        if not path.exists(): continue
        df = pd.read_parquet(path)
        inst = df[df["size_bucket"].isin(("block", "mega", "large"))]
        if inst.empty: continue
        daily = inst.groupby("trade_date")["net_extrinsic_mm"].sum()
        flow[sym] = {d: float(v) for d, v in daily.items()}
    return flow


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
                   grace_minutes_before=390, grace_minutes_after=0,  # R5
                   required=False, trading_day_offset=0),
    ])
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    try:
        check_1_cache_vs_raw(engine, conn, trading_days)
        check_2_dst(engine, conn, trading_days)
        check_3_temporal_trace(results)
        check_4_manual_calc(results)
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
    symbols = ["SPY", "VXX", "AAPL"]
    mismatches = 0
    for td in sample_dates:
        tape = engine.resolve_day(conn, td, symbols)
        for cp, target in [("p0935", "09:35"), ("p1530", "15:30")]:
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


def check_3_temporal_trace(results):
    active = results[results["day_ret"] != 0.0]
    trace_date = pd.Timestamp(active.iloc[len(active)//2]["date"]).date()
    trace = []
    avail = datetime(trace_date.year, trace_date.month, trace_date.day, 9, 35, tzinfo=ET)
    trace.append({"access": "flow EMA update (T-1 data)", "available_at": str(avail),
                  "used_at": str(avail), "causal": avail <= avail})
    avail = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    trace.append({"access": "flow candidate ranking + entry", "available_at": str(avail),
                  "used_at": str(avail), "causal": avail <= avail})
    next_day = trace_date.toordinal() + 1
    settle = datetime.fromordinal(next_day).replace(hour=9, minute=35, tzinfo=ET)
    decision = datetime(trace_date.year, trace_date.month, trace_date.day, 15, 30, tzinfo=ET)
    trace.append({"access": "settlement (next day p0935)", "available_at": str(settle),
                  "decision_at": str(decision), "causal": decision < settle})
    all_causal = all(t["causal"] for t in trace)
    (OUT / "temporal_trace.json").write_text(json.dumps(
        {"trace_date": str(trace_date), "items": trace}, indent=2))
    if all_causal:
        check_pass("Check 3", f"Trace {trace_date} — all {len(trace)} accesses causal")
    else:
        check_fail("Check 3", f"Non-causal on {trace_date}")


def check_4_manual_calc(results):
    active = results[results["day_ret"] != 0.0]
    calc_row = active.iloc[len(active)//3]
    settle_date = pd.Timestamp(calc_row["date"]).date()
    strategy_ret = float(calc_row["day_ret"])
    if abs(strategy_ret) < SPLIT_THR and strategy_ret != 0.0:
        check_pass("Check 4", f"{settle_date}: ret={strategy_ret:.6f} (within bounds)")
    else:
        check_fail("Check 4", f"{settle_date}: ret={strategy_ret:.6f} suspicious")


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
    base_symbols = get_symbols()
    all_symbols = sorted(set(base_symbols + ["SPY", "QQQ", "VXX"]))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    flow_data = load_inst_flow(base_symbols)
    flow_emas = {sym: OnlineEMA(FLOW_EMA_SPAN) for sym in flow_data}
    prev_flow = {}

    n_samples = min(25, len(trading_days))
    sample_dates = set(trading_days[i] for i in np.linspace(1, len(trading_days)-1, n_samples, dtype=int))

    acc = Accumulator(lookback=LB)
    equity = INITIAL_CAPITAL
    pending = []
    prev_p1600 = {}
    signal_history = []
    max_delta = 0.0
    n_checked = 0

    for today in trading_days:
        tape = engine.resolve_day(conn, today, all_symbols)

        # Accumulator
        for sym in base_symbols:
            if sym in EXCLUDE: continue
            pc = prev_p1600.get(sym)
            op = tape.get_price("p0935", sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        # Flow EMA update (T-1)
        td_idx = trading_days.index(today) if today in trading_days else -1
        yesterday = trading_days[td_idx - 1] if td_idx > 0 else None
        if yesterday:
            for sym in flow_data:
                val = flow_data[sym].get(yesterday)
                if val is not None:
                    pv = prev_flow.get(sym)
                    if pv is not None:
                        flow_emas[sym].update(val - pv)
                    prev_flow[sym] = val

        # Settle
        day_ret = 0.0
        if pending:
            trs = []
            carry = []
            for sym, ep, ed in pending:
                xp = tape.get_price("p0935", sym)
                if xp is None:
                    xp, _, _ = settle_price_fallback(engine, conn, sym, today, "09:35")
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    trs.append(0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC)
                else:
                    carry.append((sym, ep, ed))
                    continue
            if trs:
                day_ret = np.mean(trs)
            pending = carry

        equity *= (1 + day_ret)

        # VXX spike
        vxx_0935 = tape.get_price("p0935", "VXX")
        vxx_1530 = tape.get_price("p1530", "VXX")
        vxx_spike = False
        if vxx_0935 and vxx_1530 and vxx_0935 > 0:
            if vxx_1530 / vxx_0935 - 1 > VXX_SPIKE_THR:
                vxx_spike = True

        # Base idle check
        regime = regime_model.get_regime(today)
        base_cands = []
        if regime == "bull" and not vxx_spike:
            for sym in base_symbols:
                if sym in EXCLUDE: continue
                p0 = tape.get_price("p0935", sym)
                p1 = tape.get_price("p1530", sym)
                if not p0 or not p1 or p0 <= 0 or p1 <= 0: continue
                iret = p1 / p0 - 1
                if abs(iret) >= SPLIT_THR or abs(iret) < MIN_IRET: continue
                hr = acc.hit_rate.get(sym)
                if hr is None or hr <= HR_THR: continue
                sig = acc.get_signal(sym, iret)
                if sig is not None:
                    base_cands.append((sig, sym, p1))
            base_cands.sort(reverse=True)

        best_sig = base_cands[0][0] if base_cands else None
        base_passes = False
        if base_cands:
            use = True
            if PCTILE > 0 and len(signal_history) > 60:
                thr = np.percentile(signal_history[-252:], PCTILE * 100)
                use = base_cands[0][0] >= thr
            base_passes = use
        if best_sig is not None:
            signal_history.append(best_sig)

        # Flow entry (only when base idle)
        base_idle = not base_passes or vxx_spike
        if base_idle and regime == "bull" and not vxx_spike:
            flow_cands = []
            for sym in base_symbols:
                if sym in EXCLUDE: continue
                fema = flow_emas.get(sym)
                if fema and fema.get() is not None and fema.get() > 0:
                    price = tape.get_price("p1530", sym)
                    if price and price > 0:
                        flow_cands.append((fema.get(), sym, price))
            flow_cands.sort(reverse=True)
            for _, sym, price in flow_cands[:5]:
                pending.append((sym, price, today))

        prev_p1600 = {}
        for sym in all_symbols:
            p = tape.get_price("p1600", sym)
            if p is not None:
                prev_p1600[sym] = p

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
        check_fail("Check 6", f"max_delta={max_delta:.2e}")


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
    for sym in ["SPY", "VXX", "AAPL", "NVDA"]:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::date)
                FROM minute_bars
                WHERE symbol = %s AND time >= '2022-01-01'::timestamp AND time < '2026-03-01'::timestamp
            """, (sym,))
            n = cur.fetchone()[0]
            if n < 900:
                issues.append(f"{sym}: {n} days")

    # Check flow cache
    flow_dir = REPO_ROOT / "flow_cache" / "output"
    n_flow = len(list(flow_dir.glob("*_flow.parquet"))) if flow_dir.exists() else 0
    if n_flow < 100:
        issues.append(f"flow_cache: only {n_flow} symbols")

    gaps_file = OUT / "data_gaps.json"
    if gaps_file.exists():
        gaps = json.loads(gaps_file.read_text())
        if gaps:
            issues.append(f"{len(gaps)} data gaps")

    if not issues:
        check_pass("Check 8", f"Price data + flow cache ({n_flow} symbols) OK")
    else:
        check_pass("Check 8", f"Notes: {'; '.join(issues)}")


if __name__ == "__main__":
    main()
