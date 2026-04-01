# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""14 — Capital Allocation Fix for C2 (position overlap bug).

Bug C2: Base overnight (100%) + Gold overnight (100%) can both enter on the
same night = 200% capital deployed. This violates debit-only (max 100%).

Tests 6 approaches:
  0. bugged:       No fix (200% on overlap nights) — for comparison
  1. split_80_20:  Base always gets 80%, gold ON always gets 20%
  2. split_50_50:  On overlap nights, each gets 50%. Solo nights get 100%.
  3. base_priority: If base enters, skip gold ON. Gold ON only on base-idle nights.
  4. gold_priority: If gold ON qualifies, skip base. Base only when gold ON doesn't fire.
  5. dynamic:      Base gets N/(N+1), gold ON gets 1/(N+1) where N=base positions.

5-leg composite: base overnight + gold intraday + gold overnight + SQQQ spike + flow + cash sweep.
PhasedDay enforced. 4 checkpoints: 09:35 → 10:30 → 15:30 → 16:00.

Usage:
    python experiments/14_capital_fix/run_strategy.py
"""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict
from datetime import time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay,
    build_price_cache, load_price_cache,
)
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.indicators import OnlineEMA, Accumulator
from shared.metrics import sharpe
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_symbols

from common import (
    BASE_N, CAL_PER_TRADING, CASH_DAILY_RATE, EXCLUDE, FIX_MODES,
    FLOW_CACHE_DIR, FLOW_EMA, FLOW_N, GOLD_ID_EMA, GOLD_ON_EMA,
    HR_THR, LB, MIN_IRET, PCTILE, SPLIT_THR, SQQQ_THR, STREAK,
    VXX_LB, VXX_SPIKE_THR, get_schedule,
)

OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)

GOLD = ["GLD", "GDX", "NUGT"]


def is_split(r):
    return abs(r) >= SPLIT_THR


def compute_vxx_momentum(vxx_rets, lookback):
    if len(vxx_rets) < lookback:
        return None
    cum = 1.0
    for r in vxx_rets[-lookback:]:
        if r is not None:
            cum *= (1 + r)
    return cum - 1


def load_inst_flow(symbols):
    """Load institutional flow from flow_cache parquets."""
    flow = {}
    for sym in symbols:
        path = FLOW_CACHE_DIR / f"{sym}_flow.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        inst = df[df["size_bucket"].isin(("block", "mega", "large"))]
        if inst.empty:
            continue
        daily = inst.groupby("trade_date")["net_extrinsic_mm"].sum()
        flow[sym] = {d: float(v) for d, v in daily.items()}
    return flow


def run_with_fix(tape, trading_days, regime_model, base_symbols, flow_data, fix_mode):
    """Run composite with specified capital allocation fix."""
    acc = Accumulator(lookback=LB)
    gold_id_emas = {g: OnlineEMA(GOLD_ID_EMA) for g in GOLD}
    gold_on_emas = {g: OnlineEMA(GOLD_ON_EMA) for g in GOLD}
    flow_emas = {sym: OnlineEMA(FLOW_EMA) for sym in flow_data}
    prev_flow = {}
    vxx_rets_yesterday = []
    vxx_prev_close = None
    prev_p1600 = {}

    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    data_gaps = []
    pending_stocks = []    # (sym, price, date, source, weight)
    pending_gold_on = []   # (sym, price, date, weight)
    pending_sqqq = []      # (sym, price, date, weight)
    signal_history = []
    interest_earned = 0.0
    n_base = 0
    n_flow = 0
    n_gold_id = 0
    n_gold_on = 0
    n_sqqq = 0
    n_overlap = 0

    for today in trading_days:
        t = tape.get(today)
        if t is None:
            continue
        p0935 = t["p0935"]
        p1030 = t["p1030"]
        p1530 = t["p1530"]
        p1600 = t["p1600"]
        regime = regime_model.get_regime(today)

        # Phase 1: 09:35 — Update accumulators, settle overnights
        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1600.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        for g in GOLD:
            pc = prev_p1600.get(g)
            op = p0935.get(g)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if not is_split(r):
                    gold_id_emas[g].update(r)
                    gold_on_emas[g].update(r)

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

        # C5 FIX: snapshot equity before settlement
        equity_before = equity

        # Settle stocks (weighted, carry forward missing)
        stock_settle = 0.0
        stock_carry = []
        if pending_stocks:
            for sym, ep, ed, source, wt in pending_stocks:
                xp = p0935.get(sym)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    stock_settle += (0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC) * wt
                else:
                    stock_carry.append((sym, ep, ed, source, wt))
                    data_gaps.append({"date": str(today), "symbol": sym, "leg": source,
                        "target": "09:35", "resolution": "carry_forward"})
                    continue
                if source == "base":
                    n_base += 1
                else:
                    n_flow += 1
            pending_stocks = stock_carry

        # Settle gold overnight (weighted, carry forward missing)
        gold_on_settle = 0.0
        gold_on_carry = []
        if pending_gold_on:
            for sym, ep, ed, wt in pending_gold_on:
                xp = p0935.get(sym)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    gold_on_settle += (0.0 if is_split(rr) else rr - 2 * TC) * wt
                else:
                    gold_on_carry.append((sym, ep, ed, wt))
                    data_gaps.append({"date": str(today), "symbol": sym, "leg": "gold_on",
                        "target": "09:35", "resolution": "carry_forward"})
                    continue
                n_gold_on += 1
            pending_gold_on = gold_on_carry

        # Settle SQQQ (weighted, carry forward missing)
        sqqq_settle = 0.0
        sqqq_carry = []
        if pending_sqqq:
            for sym, ep, ed, wt in pending_sqqq:
                xp = p0935.get(sym)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    sqqq_settle += (0.0 if is_split(rr) else rr - 2 * TC) * wt
                else:
                    sqqq_carry.append((sym, ep, ed, wt))
                    data_gaps.append({"date": str(today), "symbol": sym, "leg": "sqqq",
                        "target": "09:35", "resolution": "carry_forward"})
                    continue
                n_sqqq += 1
            pending_sqqq = sqqq_carry

        morning_settle = stock_settle + gold_on_settle + sqqq_settle
        equity *= (1 + morning_settle)

        # Phase 2: 10:30 — Gold intraday entry
        gold_id_entry = None
        ev = gold_id_emas.get("NUGT", OnlineEMA(34)).get()
        if ev is not None and ev > 0:
            np_ = p1030.get("NUGT")
            if np_ and np_ > 0:
                gold_id_entry = ("NUGT", np_)

        # Phase 3: 15:30 — Build candidates, VXX spike
        vxx_0935 = p0935.get("VXX")
        vxx_1530 = p1530.get("VXX")
        vxx_spike = False
        vxx_id_ret = None
        if vxx_0935 and vxx_1530 and vxx_0935 > 0:
            vxx_id_ret = vxx_1530 / vxx_0935 - 1
            if vxx_id_ret > VXX_SPIKE_THR:
                vxx_spike = True

        base_cands = []
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
                sig = acc.get_signal(sym, iret, STREAK)
                if sig is not None:
                    base_cands.append((sig, sym, p1))
            base_cands.sort(reverse=True)

        flow_cands = []
        if regime == "bull":
            for sym in base_symbols:
                if sym in EXCLUDE:
                    continue
                fema = flow_emas.get(sym)
                if fema and fema.get() is not None and fema.get() > 0:
                    price = p1530.get(sym)
                    if price and price > 0:
                        flow_cands.append((fema.get(), sym, price))
            flow_cands.sort(reverse=True)

        best_sig = base_cands[0][0] if base_cands else None

        # Phase 4: 16:00 — Settle gold ID, enter overnights
        vxx_raw = p1600.get("VXX")
        vxx_today_ret = None
        if vxx_raw and vxx_prev_close and vxx_prev_close > 0:
            vr = vxx_raw / vxx_prev_close - 1
            vxx_today_ret = 0.0 if is_split(vr) else vr
        if vxx_raw:
            vxx_prev_close = vxx_raw

        vxx_with = vxx_rets_yesterday + ([vxx_today_ret] if vxx_today_ret is not None else [])
        vm_close = compute_vxx_momentum(vxx_with, VXX_LB)
        close_gap = None
        if regime != "bull":
            close_gap = "bear_regime"
        elif today.weekday() == 0:
            close_gap = "skip_monday"
        elif vm_close is not None and vm_close >= 0:
            close_gap = "vxx_positive"

        # Gold intraday settle
        gold_id_ret = 0.0
        if gold_id_entry:
            sym, ep = gold_id_entry
            xp = p1600.get(sym)
            if xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                if not is_split(rr):
                    gold_id_ret = rr - 2 * TC
                    n_gold_id += 1
        equity *= (1 + gold_id_ret)

        # Cash sweep
        on_idle = (morning_settle == 0.0 and not pending_stocks and not pending_gold_on and not pending_sqqq)
        id_idle = (gold_id_ret == 0.0)
        idle_frac = 1.0 if (on_idle and id_idle) else (0.67 if on_idle else (0.33 if id_idle else 0.0))
        cash_interest = 0.0
        if idle_frac > 0:
            cash_interest = equity * CASH_DAILY_RATE * CAL_PER_TRADING * idle_frac
            interest_earned += cash_interest
            equity += cash_interest

        # C5 FIX: day_return from equity change
        day_ret = equity / equity_before - 1 if equity_before > 0 else 0.0
        daily_rets.append(day_ret)
        dates.append(today)

        # Percentile gate (A4a: check THEN append)
        base_passes = False
        if base_cands:
            use = True
            if PCTILE > 0 and len(signal_history) > 60:
                thr = np.percentile(signal_history[-252:], PCTILE * 100)
                use = base_cands[0][0] >= thr
            base_passes = use
        if best_sig is not None:
            signal_history.append(best_sig)

        # Determine what wants to enter tonight
        wants_base = not vxx_spike and base_passes and len(base_cands) > 0
        wants_flow = not vxx_spike and not base_passes and len(flow_cands) > 0
        wants_gold_on = (close_gap in ("vxx_positive", "skip_monday") and
                         not pending_gold_on and
                         gold_on_emas.get("GLD", OnlineEMA(16)).get() is not None and
                         gold_on_emas["GLD"].get() > 0 and
                         p1600.get("GLD") is not None and p1600["GLD"] > 0)
        wants_sqqq = (vxx_spike and vxx_id_ret is not None and
                      vxx_id_ret > SQQQ_THR and
                      p1600.get("SQQQ") is not None and p1600["SQQQ"] > 0)

        stock_entering = wants_base or wants_flow
        if stock_entering and wants_gold_on:
            n_overlap += 1

        # Capital allocation (C2 + C8 fix)
        stock_weight = 0.0
        gold_on_weight = 0.0
        sqqq_weight = 0.0

        if fix_mode == "bugged":
            stock_weight = 1.0 if stock_entering else 0.0
            gold_on_weight = 1.0 if wants_gold_on else 0.0
            sqqq_weight = 1.0 if wants_sqqq else 0.0

        elif fix_mode == "split_80_20":
            if wants_sqqq:
                sqqq_weight = 0.80
                gold_on_weight = 0.20 if wants_gold_on else 0.0
            else:
                stock_weight = 0.80 if stock_entering else 0.0
                gold_on_weight = 0.20 if wants_gold_on else 0.0

        elif fix_mode == "split_50_50":
            if wants_sqqq and wants_gold_on:
                sqqq_weight = 0.50
                gold_on_weight = 0.50
            elif wants_sqqq:
                sqqq_weight = 1.0
            elif stock_entering and wants_gold_on:
                stock_weight = 0.50
                gold_on_weight = 0.50
            elif stock_entering:
                stock_weight = 1.0
            elif wants_gold_on:
                gold_on_weight = 1.0

        elif fix_mode == "base_priority":
            if wants_sqqq:
                sqqq_weight = 1.0
            elif stock_entering:
                stock_weight = 1.0
                gold_on_weight = 0.0
            elif wants_gold_on:
                gold_on_weight = 1.0

        elif fix_mode == "gold_priority":
            if wants_gold_on and wants_sqqq:
                gold_on_weight = 0.50
                sqqq_weight = 0.50
            elif wants_gold_on:
                gold_on_weight = 1.0
                stock_weight = 0.0
            elif wants_sqqq:
                sqqq_weight = 1.0
            elif stock_entering:
                stock_weight = 1.0

        elif fix_mode == "dynamic":
            if wants_sqqq and wants_gold_on:
                sqqq_weight = 0.50
                gold_on_weight = 0.50
            elif wants_sqqq:
                sqqq_weight = 1.0
            elif stock_entering and wants_gold_on:
                n_stocks = BASE_N if wants_base else FLOW_N
                stock_weight = n_stocks / (n_stocks + 1)
                gold_on_weight = 1.0 / (n_stocks + 1)
            elif stock_entering:
                stock_weight = 1.0
            elif wants_gold_on:
                gold_on_weight = 1.0

        # Enter stock positions
        if stock_weight > 0:
            if wants_base:
                selected = base_cands[:BASE_N]
                per_stock_wt = stock_weight / len(selected) if selected else 0
                for _, sym, price in selected:
                    pending_stocks.append((sym, price, today, "base", per_stock_wt))
            elif wants_flow:
                selected = flow_cands[:FLOW_N]
                per_stock_wt = stock_weight / len(selected) if selected else 0
                for _, sym, price in selected:
                    pending_stocks.append((sym, price, today, "flow", per_stock_wt))

        if wants_sqqq:
            pending_sqqq.append(("SQQQ", p1600["SQQQ"], today, sqqq_weight))

        if gold_on_weight > 0 and wants_gold_on:
            pending_gold_on.append(("GLD", p1600["GLD"], today, gold_on_weight))

        if vxx_today_ret is not None:
            vxx_rets_yesterday.append(vxx_today_ret)
        prev_p1600 = {s: p for s, p in p1600.items() if p is not None}

    # Metrics
    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    tts = pd.Timestamp(TRAIN_END)
    tr_sh = float(sharpe(dr[dt <= tts]))
    te_sh = float(sharpe(dr[dt > tts]))
    cum = np.cumprod(1 + dr)
    pk = np.maximum.accumulate(cum)
    mdd = float(abs((cum / pk - 1).min()) * 100)
    ret = float((equity / INITIAL_CAPITAL - 1) * 100)
    nz = [r for r in dr if r != 0]
    wr = float(np.mean([r > 0 for r in nz]) * 100) if nz else 0

    return {
        "train_sh": round(tr_sh, 3), "test_sh": round(te_sh, 3),
        "ret": round(ret, 1), "dd": round(mdd, 2), "wr": round(wr, 1),
        "n_base": n_base, "n_flow": n_flow, "n_gold_id": n_gold_id,
        "n_gold_on": n_gold_on, "n_sqqq": n_sqqq, "n_overlap": n_overlap,
        "interest": round(interest_earned, 2),
        "daily_rets": daily_rets, "dates": dates, "data_gaps": data_gaps,
    }


def main():
    base_symbols = get_symbols()
    all_symbols = sorted(set(base_symbols + GOLD + ["SPY", "QQQ", "VXX", "SQQQ"]))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    print("  Loading flow...", file=sys.stderr)
    flow_data = load_inst_flow(base_symbols)
    print(f"  Flow: {len(flow_data)} symbols", file=sys.stderr)

    schedule = get_schedule()
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    # Reference used date.today() — converted to END_DATE for reproducibility.
    # Train Sharpes match exactly; test Sharpes differ due to fewer test days.
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    # Build or load price cache
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(all_symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    conn.close()

    # Build tape from price cache
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        p0935 = {}
        p1030 = {}
        p1530 = {}
        p1600 = {}
        for sym, prices in day_data.items():
            for cp, target in [("p0935", p0935), ("p1030", p1030), ("p1530", p1530), ("p1600", p1600)]:
                v = prices.get(cp)
                if v is not None:
                    target[sym] = v
        tape[td] = {"p0935": p0935, "p1030": p1030, "p1530": p1530, "p1600": p1600}

    # SPY B&H benchmark (close-to-close via p1600)
    spy_day_rets = {}
    prev_spy = None
    for td in trading_days:
        spy_p = tape.get(td, {}).get("p1600", {}).get("SPY")
        if spy_p and prev_spy and prev_spy > 0:
            r = spy_p / prev_spy - 1
            if abs(r) < SPLIT_THR:
                spy_day_rets[td] = r
        if spy_p:
            prev_spy = spy_p

    # Run all 6 configs
    # NOTE: regime_model is shared across configs. Config 1 fits the HMM;
    # configs 2-6 reuse the cached model (matching reference behavior).
    # The regime cache is saved after config 1 for verification.
    results = {}
    print(f"\n{'='*120}", file=sys.stderr)
    print(f"  14 — CAPITAL ALLOCATION FIX (C2: position overlap)", file=sys.stderr)
    print(f"{'='*120}", file=sys.stderr)
    print(f"  {'Fix':<16s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'GldON':>5s}  {'Overlap':>7s}", file=sys.stderr)

    config_series = {}
    regime_cache_saved = False
    for fix_mode in tqdm(FIX_MODES, desc="Configs", file=sys.stderr):
        m = run_with_fix(tape, trading_days, regime_model, base_symbols, flow_data, fix_mode)
        # Save regime cache after first config (model is fitted)
        if not regime_cache_saved:
            regime_by_day = {}
            for td in trading_days:
                regime_by_day[td] = regime_model.get_regime(td)
            pd.DataFrame([{"date": d, "regime": r} for d, r in regime_by_day.items()]).to_parquet(
                OUT / "regime_cache.parquet", index=False)
            regime_cache_saved = True
        config_series[fix_mode] = (m["daily_rets"], m["dates"], m.get("data_gaps", []))
        results[fix_mode] = {k: v for k, v in m.items() if k not in ("daily_rets", "dates", "data_gaps")}
        print(f"  {fix_mode:<16s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.2f}  {m['ret']:>+7.0f}%  {m['dd']:>5.1f}%  {m['wr']:>4.0f}%  {m['n_gold_on']:>5d}  {m['n_overlap']:>7d}", file=sys.stderr)

    print(f"{'='*120}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for fix_mode, (rets, dts, _gaps) in config_series.items():
        dr = np.array(rets)
        pd.DataFrame({
            "date": dts,
            "day_ret": rets,
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{fix_mode}.parquet", index=False)

    # Bookkeeping for primary config (gold_priority — best test Sharpe in reference)
    primary = "gold_priority"
    p_rets, p_dates, p_gaps = config_series[primary]
    p_dr = np.array(p_rets)
    p_nz = [r for r in p_dr if r != 0]
    p_nw = sum(1 for r in p_nz if r > 0)
    p_nl = sum(1 for r in p_nz if r < 0)
    n_trades = results[primary]["n_base"] + results[primary]["n_flow"] + results[primary]["n_gold_id"] + results[primary]["n_gold_on"] + results[primary]["n_sqqq"]
    metrics = compute_experiment_metrics(p_rets, p_dates, n_trades, p_nw, p_nl)
    print_results("14 CAPITAL FIX (gold_priority)", metrics)
    save_results(OUT, primary, p_rets, p_dates, metrics, p_gaps)
    plot_pnl(OUT, "Capital Fix (gold_priority)", p_rets, p_dates,
             trading_days, spy_day_rets, metrics, color="#3b82f6")

    # Statistical robustness on primary config
    from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
    spy_all = np.array([spy_day_rets.get(d, 0.0) for d in p_dates])
    stat_results = {
        "permutation": permutation_test(p_dr, spy_all, tc_per_active_day=2 * TC),
        "bootstrap": bootstrap_sharpe_ci(p_dr),
        "concentration": concentration_ratio(p_dr),
    }
    (OUT / "stat_tests.json").write_text(json.dumps(stat_results, indent=2, default=str))
    for sname, res in stat_results.items():
        status = "PASS" if res.get("pass") else "FAIL" if res.get("pass") is False else "N/A"
        print(f"  {sname}: {status} — {res.get('interpretation', '')}", file=sys.stderr)


if __name__ == "__main__":
    main()
