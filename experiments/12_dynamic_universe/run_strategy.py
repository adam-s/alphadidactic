# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""175 — Dynamic Liquidity Filter: overnight momentum on top-N liquid R1000 stocks.

911 symbols from R1000 universe. Each day, rank by trailing 60-day
activity_rate × avg_price_level. Only top-N eligible for signal ranking.
Single position (top-1 signal), overnight hold (p1530 → p0935 next day).
Accumulator with hit_rate, avg_pos, streak (same signal as base overnight).
Regime gate (bull only). Percentile gate at 50th.
PhasedDay enforced, pending_row pattern.

Usage:
    python experiments/12_dynamic_universe/run_strategy.py [--top-n 100]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from collections import deque
from datetime import date, time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay, Checkpoint,
    ResolutionMode, build_schedule, settle_price_fallback,
    build_price_cache, load_price_cache,
)
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.indicators import Accumulator
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_r1000_symbols

OUT = HERE / "output"
EXCLUDE = {"SPY", "QQQ", "VXX"}

# Parameters (same as base overnight)
STREAK = 0.75; HR_THR = 0.57; LB = 80; PCTILE = 0.50; MIN_IRET = 0.013
VOLUME_LOOKBACK = 60


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-n", type=int, default=100)
    args = parser.parse_args()
    top_n = args.top_n

    r1000 = get_r1000_symbols()
    all_stocks = [s for s in r1000 if s not in EXCLUDE]
    all_symbols = sorted(set(all_stocks + ["SPY", "VXX"]))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)

    schedule = build_schedule("exp12_dynuniv", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        # p1600 for SPY B&H benchmark only
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=390, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
    ])
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(all_symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    # Activity and price tracking for liquidity ranking
    activity = {sym: deque(maxlen=VOLUME_LOOKBACK) for sym in all_stocks}
    price_level = {sym: deque(maxlen=VOLUME_LOOKBACK) for sym in all_stocks}

    acc = Accumulator(lookback=LB)
    equity = INITIAL_CAPITAL
    daily_rets = []; dates = []; spy_day_rets = {}
    pending = None; prev_p1530 = {}; signal_history = []; trade_log = []
    n_trades = 0; n_wins = 0; n_losses = 0; data_gaps = []

    try:
        for today in tqdm(trading_days, desc=f"12 DynUniv top{top_n}", file=sys.stderr):
            phased = CachedPhasedDay(price_cache, today, schedule)

            # Phase 1: 09:35
            m = phased.resolve_up_to(clock_time(9, 35))
            p0935 = m.get("p0935", {})

            # Update accumulator with overnight returns (p1530_prev → p0935_today)
            for sym in all_stocks:
                pc = prev_p1530.get(sym)
                op = p0935.get(sym)
                if pc and op and pc > 0 and op > 0:
                    r = op / pc - 1
                    if abs(r) < SPLIT_THRESHOLD:
                        acc.update(sym, r)

            # Settle pending overnight position
            day_ret = 0.0
            if pending is not None:
                sym, ep, ed = pending
                if ed >= today:
                    raise AssertionError(f"TEMPORAL: {sym} {ed} vs {today}")
                xp = p0935.get(sym)
                if xp is None:
                    xp_fb, rt, res = settle_price_fallback(engine, conn, sym, today, "09:35")
                    if xp_fb is not None:
                        xp = xp_fb
                        data_gaps.append({"date": str(today), "symbol": sym,
                            "target": "09:35", "resolved": rt, "resolution": res, "price": xp})
                        print(f"  GAP: {sym} {today} — {res} at {rt}", file=sys.stderr)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    day_ret = 0.0 if abs(rr) >= SPLIT_THRESHOLD else rr - 2 * TC
                n_trades += 1
                if day_ret > 0: n_wins += 1
                elif day_ret < 0: n_losses += 1
                trade_log.append({"entry_date": str(ed), "entry_checkpoint": "p1530",
                    "settle_date": str(today), "settle_checkpoint": "p0935",
                    "symbol": sym, "entry_price": ep, "exit_price": xp, "return": day_ret})
                pending = None

            equity *= (1 + day_ret)
            daily_rets.append(day_ret)
            dates.append(today)

            # Phase 2: 15:30
            aft = phased.resolve_up_to(clock_time(15, 30))
            p1530 = aft.get("p1530", {})

            # Update activity tracking (causal: using today's observable prices)
            for sym in all_stocks:
                has_prices = sym in p0935 and sym in p1530
                activity[sym].append(1.0 if has_prices else 0.0)
                if sym in p1530 and p1530[sym] and p1530[sym] > 0:
                    price_level[sym].append(p1530[sym])

            # Compute liquidity rank
            liquidity_scores = {}
            for sym in all_stocks:
                if len(activity[sym]) < 20:
                    continue
                act_rate = sum(activity[sym]) / len(activity[sym])
                avg_price = np.mean(list(price_level[sym])) if len(price_level[sym]) > 5 else 0
                if act_rate > 0.5 and avg_price > 5:
                    liquidity_scores[sym] = act_rate * avg_price

            ranked = sorted(liquidity_scores.items(), key=lambda x: x[1], reverse=True)
            eligible = set(sym for sym, _ in ranked[:top_n])

            # Build candidates from eligible universe
            regime = regime_model.get_regime(today)
            cands = []
            if regime == "bull":
                for sym in eligible:
                    p0 = p0935.get(sym)
                    p1 = p1530.get(sym)
                    if not p0 or not p1 or p0 <= 0 or p1 <= 0:
                        continue
                    iret = p1 / p0 - 1
                    if abs(iret) >= SPLIT_THRESHOLD or abs(iret) < MIN_IRET:
                        continue
                    hr = acc.hit_rate.get(sym)
                    if hr is None or hr <= HR_THR:
                        continue
                    sig = acc.get_signal(sym, iret, STREAK)
                    if sig is not None:
                        cands.append((sig, sym, p1))
                cands.sort(reverse=True)

            best_sig = cands[0][0] if cands else None

            # Phase 3: 16:00 (SPY B&H only)
            cl = phased.resolve_up_to(clock_time(16, 0))
            p1600 = cl.get("p1600", {})

            # Percentile gate
            if cands:
                use = True
                if PCTILE > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], PCTILE * 100)
                    use = cands[0][0] >= thr
                if use:
                    pending = (cands[0][1], cands[0][2], today)
            if best_sig is not None:
                signal_history.append(best_sig)

            # SPY B&H
            sp = prev_p1530.get("SPY")
            sc = p1600.get("SPY")
            if sp and sc and sp > 0:
                r = sc / sp - 1
                if abs(r) < SPLIT_THRESHOLD:
                    spy_day_rets[today] = r

            prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

    finally:
        conn.close()

    # === Bookkeeping ===
    metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
    print_results(f"12 DYN UNIVERSE (top{top_n})", metrics)
    save_results(OUT, f"dynamic_universe_top{top_n}", daily_rets, dates, metrics, data_gaps, trade_log)

    # === Statistical Robustness ===
    import json
    from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
    dr = np.array(daily_rets)
    spy_all = np.array([spy_day_rets.get(d, 0.0) for d in dates])
    stat_results = {
        "permutation": permutation_test(dr, spy_all, tc_per_active_day=2 * TC),
        "bootstrap": bootstrap_sharpe_ci(dr),
        "concentration": concentration_ratio(dr),
    }
    (OUT / "stat_tests.json").write_text(json.dumps(stat_results, indent=2, default=str))
    for name, res in stat_results.items():
        status = "PASS" if res.get("pass") else "FAIL" if res.get("pass") is False else "N/A"
        print(f"  {name}: {status} — {res.get('interpretation', '')}", file=sys.stderr)
    plot_pnl(OUT, f"Dynamic Universe (top{top_n})", daily_rets, dates, trading_days, spy_day_rets,
             metrics, color="#7c3aed")


if __name__ == "__main__":
    main()
