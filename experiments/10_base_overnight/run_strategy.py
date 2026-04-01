# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""186 Leg 1 — Base Overnight Momentum (standalone).

153 symbols from get_symbols(). Entry at 15:30, exit at 09:35 next day.
Regime gate (bull only), VXX kill switch (VXX intraday >3% = skip).
Accumulator with hit_rate, avg_pos, streak.
Signal: iret * avg_pos * (1 + 0.75*streak) * hit_rate
Percentile gate at 50th percentile, min_iret=0.013, hr_thr=0.57.
Top 5 stocks, equal weight. PhasedDay enforced, pending_row pattern.
"""
from __future__ import annotations

import sys, warnings
from collections import defaultdict
from datetime import date, time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, END_DATE, is_split
from shared.cursor_engine import CursorEngine, MinuteBarsSource, CachedPhasedDay, Checkpoint, ResolutionMode, build_schedule, settle_price_fallback, build_price_cache, load_price_cache
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_symbols

OUT = HERE / "output"
EXCLUDE = {"SPY", "QQQ", "VXX"}

# Parameters
STREAK = 0.75; HR_THR = 0.57; LB = 80; PCTILE = 0.50; MIN_IRET = 0.013
VXX_SPIKE_THR = 0.03


class Accumulator:
    def __init__(self, lookback=80):
        self.lookback = lookback
        self.rets = defaultdict(list); self.hit_rate = {}; self.avg_pos = {}; self.streak = {}
    def update(self, sym, ret):
        self.rets[sym].append(ret); r = self.rets[sym]
        if len(r) < 20: self.hit_rate.pop(sym, None); self.avg_pos.pop(sym, None); self.streak[sym] = 0; return
        recent = r[-self.lookback:]; pos = [x for x in recent if x > 0]
        self.hit_rate[sym] = len(pos) / len(recent)
        self.avg_pos[sym] = float(np.mean(pos)) if pos else 0.0
        s = 0
        for x in reversed(r):
            if x > 0: s += 1
            else: break
        self.streak[sym] = s
    def get_signal(self, sym, iret, streak_mult):
        if sym not in self.hit_rate: return None
        return iret * self.avg_pos.get(sym, 0.0) * (1 + streak_mult * self.streak.get(sym, 0)) * self.hit_rate[sym]


def main():
    base_symbols = get_symbols()
    all_symbols = sorted(set(base_symbols + ["SPY", "QQQ", "VXX"]))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH); fred_panel.index = pd.to_datetime(fred_panel.index)

    schedule = build_schedule("leg1_base", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
        Checkpoint(name="p1530", target_time_et=clock_time(15, 30), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0, required=False, trading_day_offset=0),
        # R5: grace=390 covers half-day closes at 13:00 ET. Stale prev_p1600 contaminates accumulator.
        Checkpoint(name="p1600", target_time_et=clock_time(16, 0), mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=390, grace_minutes_after=0, required=False, trading_day_offset=0),
    ])
    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    # Per-experiment price cache
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    acc = Accumulator(lookback=LB)
    equity = INITIAL_CAPITAL; daily_rets = []; dates = []; spy_day_rets = {}
    pending = []; prev_p1600 = {}; signal_history = []
    n_trades = 0; n_wins = 0; n_losses = 0; data_gaps = []

    try:
        for today in tqdm(trading_days, desc="186 Leg1 Base ON", file=sys.stderr):
            phased = CachedPhasedDay(price_cache, today, schedule)

            # Phase 1: 09:35
            m = phased.resolve_up_to(clock_time(9, 35))
            p0935 = m.get("p0935", {})

            # Update accumulator with overnight returns
            for sym in base_symbols:
                if sym in EXCLUDE: continue
                pc = prev_p1600.get(sym); op = p0935.get(sym)
                if pc and op and pc > 0 and op > 0:
                    r = op / pc - 1
                    if abs(r) < SPLIT_THRESHOLD: acc.update(sym, r)

            # Settle pending overnight positions
            day_ret = 0.0
            if pending:
                trs = []; carry = []
                for sym, ep, ed in pending:
                    if ed >= today: raise AssertionError(f"TEMPORAL: {sym} {ed} vs {today}")
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
                        trs.append(0.0 if abs(rr) >= SPLIT_THRESHOLD else rr - 2 * TC)
                    else:
                        carry.append((sym, ep, ed)); continue
                    n_trades += 1
                    if trs[-1] > 0: n_wins += 1
                    elif trs[-1] < 0: n_losses += 1
                if trs: day_ret = np.mean(trs)
                pending = carry

            equity *= (1 + day_ret); daily_rets.append(day_ret); dates.append(today)

            # Phase 2: 15:30
            aft = phased.resolve_up_to(clock_time(15, 30))
            p1530 = aft.get("p1530", {})

            # VXX intraday kill switch
            vxx_0935 = p0935.get("VXX"); vxx_1530 = p1530.get("VXX")
            vxx_spike = False
            if vxx_0935 and vxx_1530 and vxx_0935 > 0:
                vxx_id_ret = vxx_1530 / vxx_0935 - 1
                if vxx_id_ret > VXX_SPIKE_THR:
                    vxx_spike = True

            # Build candidates
            regime = regime_model.get_regime(today)
            base_cands = []
            if regime == "bull" and not vxx_spike:
                for sym in base_symbols:
                    if sym in EXCLUDE: continue
                    p0 = p0935.get(sym); p1 = p1530.get(sym)
                    if not p0 or not p1 or p0 <= 0 or p1 <= 0: continue
                    iret = p1 / p0 - 1
                    if abs(iret) >= SPLIT_THRESHOLD or abs(iret) < MIN_IRET: continue
                    hr = acc.hit_rate.get(sym)
                    if hr is None or hr <= HR_THR: continue
                    sig = acc.get_signal(sym, iret, STREAK)
                    if sig is not None: base_cands.append((sig, sym, p1))
                base_cands.sort(reverse=True)

            best_sig = base_cands[0][0] if base_cands else None

            # Phase 3: 16:00 (for SPY B&H and close prices)
            cl = phased.resolve_up_to(clock_time(16, 0))
            p1600 = cl.get("p1600", {})

            # Percentile gate
            base_passes = False
            if base_cands:
                use = True
                if PCTILE > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], PCTILE * 100)
                    use = base_cands[0][0] >= thr
                base_passes = use
            if best_sig is not None: signal_history.append(best_sig)

            # Entry decision — use p1530 prices for entry (entry at 15:30)
            if base_passes and not vxx_spike:
                for _, sym, price in base_cands[:5]:
                    pending.append((sym, price, today))

            # SPY B&H
            sp = prev_p1600.get("SPY"); sc = p1600.get("SPY")
            if sp and sc and sp > 0:
                r = sc / sp - 1
                if abs(r) < SPLIT_THRESHOLD: spy_day_rets[today] = r

            prev_p1600 = {s: p for s, p in p1600.items() if p is not None}

    finally:
        conn.close()

    # === Bookkeeping ===
    metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
    print_results("10 BASE OVERNIGHT", metrics)
    save_results(OUT, "base_overnight", daily_rets, dates, metrics, data_gaps)
    plot_pnl(OUT, "Base Overnight", daily_rets, dates, trading_days, spy_day_rets,
             metrics, color="#2563eb")


if __name__ == "__main__":
    main()
