"""09 Gold Intraday — NUGT intraday when GLD overnight EMA > 0.

Signal: EMA-34 of GLD overnight returns > 0.
Entry: NUGT at 10:30 ET. Exit: NUGT at 16:00 ET same day.
No pending-row needed (same-day intraday trade).
PhasedDay enforced.
"""
from __future__ import annotations

import sys
import warnings
from datetime import time as clock_time
from pathlib import Path

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

OUT = HERE / "output"

# --- Parameters ---
GOLD_ID_EMA = 34


def main():
    gold_symbols = ["GLD", "GDX", "NUGT"]
    all_symbols = sorted(set(gold_symbols + ["SPY"]))

    schedule = build_schedule("gold_id", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1030", target_time_et=clock_time(10, 30),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        # R5: grace_minutes_before=390 covers half-day closes at 13:00 ET.
        # prev_p1600 feeds next morning's EMA — stale close contaminates EMA.
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

    gold_id_emas = {g: OnlineEMA(GOLD_ID_EMA) for g in gold_symbols}
    prev_p1600 = {}

    daily_rets = []
    dates = []
    spy_day_rets = {}
    n_trades = 0
    n_wins = 0
    n_losses = 0
    data_gaps = []

    try:
        for today in tqdm(trading_days, desc="09 Gold ID", file=sys.stderr):
            phased = CachedPhasedDay(price_cache, today, schedule)

            # === Phase 1: 09:35 — Update overnight EMAs ===
            m = phased.resolve_up_to(clock_time(9, 35))
            p0935 = m.get("p0935", {})

            for g in gold_symbols:
                pc = prev_p1600.get(g)
                op = p0935.get(g)
                if pc and op and pc > 0 and op > 0:
                    r = op / pc - 1
                    if not is_split(r):
                        gold_id_emas[g].update(r)

            # === Phase 2: 10:30 — Enter NUGT if GLD EMA > 0 ===
            m1030 = phased.resolve_up_to(clock_time(10, 30))
            p1030 = m1030.get("p1030", {})

            gold_id_entry = None
            ev = gold_id_emas.get("GLD", OnlineEMA(34)).get()
            if ev is not None and ev > 0:
                nugt_price = p1030.get("NUGT")
                if nugt_price and nugt_price > 0:
                    gold_id_entry = ("NUGT", nugt_price)

            # === Phase 3: 16:00 — Settle intraday ===
            cl = phased.resolve_up_to(clock_time(16, 0))
            p1600 = cl.get("p1600", {})

            day_ret = 0.0
            if gold_id_entry:
                sym, ep = gold_id_entry
                xp = p1600.get(sym)
                # Settlement fallback for half-day closes (13:00 ET on holidays)
                if xp is None:
                    xp, rt, res = settle_price_fallback(engine, conn, sym, today, "16:00")
                    if xp is not None:
                        data_gaps.append({"date": str(today), "symbol": sym,
                            "target": "16:00", "resolved": rt, "resolution": res, "price": xp})
                        print(f"  GAP: {sym} {today} — {res} at {rt}", file=sys.stderr)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    if not is_split(rr):
                        day_ret = rr - 2 * TC
                        n_trades += 1
                        if day_ret > 0: n_wins += 1
                        elif day_ret < 0: n_losses += 1

            daily_rets.append(day_ret)
            dates.append(today)

            # SPY benchmark
            sp = prev_p1600.get("SPY")
            sc = p1600.get("SPY")
            if sp and sc and sp > 0:
                r = sc / sp - 1
                if abs(r) < SPLIT_THRESHOLD:
                    spy_day_rets[today] = r

            prev_p1600 = {s: p for s, p in p1600.items() if p is not None}

    finally:
        conn.close()

    # === Bookkeeping ===
    metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
    print_results("09 GOLD INTRADAY", metrics)
    save_results(OUT, "gold_intraday", daily_rets, dates, metrics, data_gaps)
    plot_pnl(OUT, "Gold Intraday (NUGT)", daily_rets, dates, trading_days, spy_day_rets,
             metrics, color="#eab308")


if __name__ == "__main__":
    main()
