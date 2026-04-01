# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""15 — Cross-Sectional Gold: rank precious metals, pick best trending each day.

Instead of always trading NUGT, rank GLD/GDX/NUGT/SLV/SIL by their EMA value
and pick the strongest-trending one. This selects the best precious metal
instrument dynamically based on which has the most positive momentum.

Signal: OnlineEMA(span) of overnight returns, computed at 09:35 (causal).
Entry: 10:30, Exit: 16:00 (intraday, same-day settle).
Selection: top-1 or top-2 by EMA value among instruments with EMA > 0.

Usage:
    python experiments/15_cross_sectional_gold/run_strategy.py
"""
from __future__ import annotations

import json
import sys
import warnings
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
from shared.indicators import OnlineEMA
from shared.metrics import sharpe
from shared.research_core import INITIAL_CAPITAL

from common import ALL_PM, CONFIGS, SPLIT_THR, get_schedule

OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)


def is_split(r):
    return abs(r) >= SPLIT_THR


def main():
    symbols = sorted(set(ALL_PM + ["SPY", "VXX"]))
    all_spans = sorted(set(c["ema_span"] for c in CONFIGS.values()))

    schedule = get_schedule()
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    # Reference used date.today() — converted to END_DATE for reproducibility.
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    # Build or load price cache
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    conn.close()

    # Build tape from price cache
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        day_prices = {}
        for cp in ["p0935", "p1030", "p1600"]:
            prices = {}
            for sym in symbols:
                v = day_data.get(sym, {}).get(cp)
                if v is not None:
                    prices[sym] = v
            day_prices[cp] = prices
        tape[td] = day_prices

    # EMAs: {(instrument, span): OnlineEMA}
    emas = {(inst, span): OnlineEMA(span) for inst in ALL_PM for span in all_spans}
    prev_close = {}
    spy_day_rets = {}

    data_gaps = []

    class State:
        def __init__(self):
            self.equity = INITIAL_CAPITAL
            self.daily_rets = []
            self.dates = []
            self.n_trades = 0
            self.n_wins = 0
            self.n_losses = 0

    states = {name: State() for name in CONFIGS}

    for today in tqdm(trading_days, desc="15 XSec Gold", file=sys.stderr):
        td = tape.get(today)
        if td is None:
            continue
        p0935 = td.get("p0935", {})
        p1030 = td.get("p1030", {})
        p1600 = td.get("p1600", {})

        # SPY benchmark (close-to-close via p1600)
        sp = prev_close.get("SPY")
        so = p1600.get("SPY")
        if sp and so and sp > 0:
            r = so / sp - 1
            if abs(r) < SPLIT_THR:
                spy_day_rets[today] = r

        # Update EMAs with overnight returns (p0935 / prev_p1600)
        for inst in ALL_PM:
            pc = prev_close.get(inst)
            op = p0935.get(inst)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if not is_split(r):
                    for span in all_spans:
                        emas[(inst, span)].update(r)

        # Per-config: rank and select
        for name, cfg in CONFIGS.items():
            st = states[name]
            span = cfg["ema_span"]
            top_n = cfg["top_n"]

            # Rank instruments by EMA value
            candidates = []
            for inst in cfg["instruments"]:
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
                            st.n_trades += 1
                            if rr - 2 * TC > 0:
                                st.n_wins += 1
                            elif rr - 2 * TC < 0:
                                st.n_losses += 1
                    elif ep and not xp:
                        # C-exit: selected instrument has entry but no exit
                        data_gaps.append({"date": str(today), "symbol": inst,
                            "target": "16:00", "resolution": "skipped",
                            "config": name})
                if trs:
                    day_ret = np.mean(trs)

            st.equity *= (1 + day_ret)
            st.daily_rets.append(day_ret)
            st.dates.append(today)

        # Store close prices (p1600 preferred, fallback to earlier)
        prev_close = {}
        for cp_name in ["p1600", "p1030", "p0935"]:
            for sym, p in td.get(cp_name, {}).items():
                prev_close[sym] = p

    # Results
    train_end_ts = pd.Timestamp(TRAIN_END)
    results = {}
    config_series = {}

    print(f"\n{'='*100}", file=sys.stderr)
    print(f"  15 — CROSS-SECTIONAL GOLD RANKING", file=sys.stderr)
    print(f"{'='*100}", file=sys.stderr)
    print(f"  {'Config':<25s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'N':>5s}", file=sys.stderr)
    print(f"  {'-'*25}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*5}", file=sys.stderr)

    for name, st in sorted(states.items(),
        key=lambda x: min(float(sharpe(np.array(x[1].daily_rets)[pd.to_datetime(x[1].dates) <= train_end_ts])),
                          float(sharpe(np.array(x[1].daily_rets)[pd.to_datetime(x[1].dates) > train_end_ts]))), reverse=True):
        dr = np.array(st.daily_rets)
        dt = pd.to_datetime(st.dates)
        tr, te = dr[dt <= train_end_ts], dr[dt > train_end_ts]
        cum = np.cumprod(1 + dr)
        pk = np.maximum.accumulate(cum)
        mdd = float(abs((cum / pk - 1).min()) * 100)
        full_ret = float((np.prod(1 + dr) - 1) * 100)
        nz = [r for r in dr if r != 0]
        wr = float(np.mean([r > 0 for r in nz]) * 100) if nz else 0
        tr_sh = round(float(sharpe(tr)), 3)
        te_sh = round(float(sharpe(te)), 3)
        results[name] = {"train_sh": tr_sh, "test_sh": te_sh, "ret": round(full_ret, 1),
                         "dd": round(mdd, 2), "wr": round(wr, 1), "n": st.n_trades}
        config_series[name] = (st.daily_rets, st.dates, st.n_trades, st.n_wins, st.n_losses)
        print(f"  {name:<25s}  {tr_sh:>+5.2f}  {te_sh:>+5.02f}  {full_ret:>+7.0f}%  {mdd:>5.1f}%  {wr:>4.0f}%  {st.n_trades:>5d}", file=sys.stderr)

    print(f"{'='*100}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for name, (rets, dts, nt, nw, nl) in config_series.items():
        dr = np.array(rets)
        pd.DataFrame({
            "date": dts, "day_ret": rets,
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{name}.parquet", index=False)

    # Bookkeeping for primary config (best1_gold3 — best test Sharpe in reference)
    primary = "best1_gold3"
    p_rets, p_dates, p_nt, p_nw, p_nl = config_series[primary]
    metrics = compute_experiment_metrics(p_rets, p_dates, p_nt, p_nw, p_nl)
    print_results("15 XSEC GOLD (best1_gold3)", metrics)
    save_results(OUT, primary, p_rets, p_dates, metrics, data_gaps)
    plot_pnl(OUT, "Cross-Sectional Gold (best1_gold3)", p_rets, p_dates,
             trading_days, spy_day_rets, metrics, color="#f59e0b")

    # Statistical robustness
    from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
    p_dr = np.array(p_rets)
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
