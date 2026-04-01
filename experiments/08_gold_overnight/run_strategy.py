"""08 Gold Overnight — GLD overnight gated by EMA + VXX momentum + skip Monday.

Signal: GLD overnight EMA-16 > 0, VXX momentum positive or Monday gap.
Entry: GLD at 16:00 ET. Exit: GLD at 09:35 ET next trading day.
Regime gate (MacroRegime: only enter during bull).
Pending-row pattern enforced.
"""
from __future__ import annotations

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

from shared.config import SPLIT_THRESHOLD, TC, END_DATE, is_split
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay, Checkpoint,
    ResolutionMode, build_schedule, settle_price_fallback,
    build_price_cache, load_price_cache,
)
from shared.experiment_results import (
    compute_experiment_metrics, print_results, save_results, plot_pnl,
)
from shared.indicators import OnlineEMA
from shared.research_core import FRED_PANEL_PATH, MacroRegime

OUT = HERE / "output"

# --- Parameters ---
GOLD_ON_EMA = 16
VXX_LB = 20


def compute_vxx_momentum(vxx_rets, lookback):
    if len(vxx_rets) < lookback:
        return None
    cum = 1.0
    for r in vxx_rets[-lookback:]:
        cum *= (1 + r)
    return cum - 1


def main():
    all_symbols = sorted({"GLD", "SPY", "VXX"})

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)

    schedule = build_schedule("gold_on", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        # R5: grace_minutes_before=390 covers half-day closes at 13:00 ET.
        # Without this, prev_p1600 stays stale on half-days, contaminating
        # the next morning's EMA overnight return calculation.
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
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)

    gold_on_ema = OnlineEMA(GOLD_ON_EMA)
    vxx_rets_history = []
    vxx_prev_close = None
    prev_p1600 = {}

    daily_rets = []
    dates = []
    spy_day_rets = {}
    pending = None
    n_trades = 0
    n_wins = 0
    n_losses = 0
    data_gaps = []

    try:
        for today in tqdm(trading_days, desc="08 Gold ON", file=sys.stderr):
            phased = CachedPhasedDay(price_cache, today, schedule)

            # === Phase 1: 09:35 ===
            m = phased.resolve_up_to(clock_time(9, 35))
            p0935 = m.get("p0935", {})

            # Update gold overnight EMA (observable: prev close → today open)
            gld_pc = prev_p1600.get("GLD")
            gld_op = p0935.get("GLD")
            if gld_pc and gld_op and gld_pc > 0 and gld_op > 0:
                r = gld_op / gld_pc - 1
                if not is_split(r):
                    gold_on_ema.update(r)

            # Settle pending overnight
            day_ret = 0.0
            if pending:
                sym, ep, ed = pending
                assert ed < today, f"TEMPORAL: {sym} {ed} vs {today}"
                xp = p0935.get(sym)
                if xp is None:
                    xp, rt, res = settle_price_fallback(engine, conn, sym, today, "09:35")
                    if xp is not None:
                        data_gaps.append({"date": str(today), "symbol": sym,
                            "target": "09:35", "resolved": rt, "resolution": res, "price": xp})
                        print(f"  GAP: {sym} {today} — {res} at {rt}", file=sys.stderr)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    day_ret = 0.0 if is_split(rr) else rr - 2 * TC
                n_trades += 1
                if day_ret > 0: n_wins += 1
                elif day_ret < 0: n_losses += 1
                pending = None

            daily_rets.append(day_ret)
            dates.append(today)

            # === Phase 2: 16:00 ===
            cl = phased.resolve_up_to(clock_time(16, 0))
            p1600 = cl.get("p1600", {})

            # VXX close-to-close return (includes today)
            vxx_raw = p1600.get("VXX")
            vxx_today_ret = None
            if vxx_raw and vxx_prev_close and vxx_prev_close > 0:
                vr = vxx_raw / vxx_prev_close - 1
                vxx_today_ret = 0.0 if is_split(vr) else vr
            if vxx_raw:
                vxx_prev_close = vxx_raw

            # VXX momentum including today
            vxx_with = vxx_rets_history + ([vxx_today_ret] if vxx_today_ret is not None else [])
            vm_close = compute_vxx_momentum(vxx_with, VXX_LB)

            # Entry conditions: regime + VXX+/Monday gap + EMA > 0
            regime = regime_model.get_regime(today)
            close_gap = None
            if regime != "bull": close_gap = "bear_regime"
            elif today.weekday() == 0: close_gap = "skip_monday"
            elif vm_close is not None and vm_close >= 0: close_gap = "vxx_positive"

            if pending is None and close_gap in ("vxx_positive", "skip_monday"):
                gev = gold_on_ema.get()
                if gev is not None and gev > 0:
                    gp = p1600.get("GLD")
                    if gp and gp > 0:
                        pending = ("GLD", gp, today)

            # SPY benchmark
            sp = prev_p1600.get("SPY")
            sc = p1600.get("SPY")
            if sp and sc and sp > 0:
                r = sc / sp - 1
                if abs(r) < SPLIT_THRESHOLD:
                    spy_day_rets[today] = r

            if vxx_today_ret is not None:
                vxx_rets_history.append(vxx_today_ret)
            prev_p1600 = {s: p for s, p in p1600.items() if p is not None}

    finally:
        conn.close()

    # === Bookkeeping ===
    metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
    print_results("08 GOLD OVERNIGHT", metrics)
    save_results(OUT, "gold_overnight", daily_rets, dates, metrics, data_gaps)
    plot_pnl(OUT, "Gold Overnight", daily_rets, dates, trading_days, spy_day_rets,
             metrics, color="#ca8a04")


if __name__ == "__main__":
    main()
