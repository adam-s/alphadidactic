"""07 SQQQ Spike — Enter SQQQ overnight when VXX intraday spike > threshold.

Signal: VXX intraday return (09:35→15:30) exceeds VXX_SPIKE_THR.
Entry: SQQQ at 16:00 ET.
Exit: SQQQ at 09:35 ET next trading day.
Single position, no cross-section. Pending-row pattern enforced.
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

OUT = HERE / "output"

# --- Parameters ---
VXX_SPIKE_THR = 0.03



def main():
    symbols = sorted({"SPY", "VXX", "SQQQ"})

    schedule = build_schedule("sqqq_spike", [
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
    ])

    engine = CursorEngine(MinuteBarsSource(), schedule)
    conn = engine.source.get_connection()
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    # Per-experiment price cache: build on first run, reuse on subsequent runs
    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        build_price_cache(engine, conn, trading_days, symbols, cache_path)
    price_cache = load_price_cache(cache_path)

    daily_rets = []
    dates = []
    spy_day_rets = {}
    pending = None  # (symbol, entry_price, entry_date)
    prev_p1600 = {}
    n_trades = 0
    n_wins = 0
    n_losses = 0
    data_gaps = []

    try:
        for today in tqdm(trading_days, desc="07 SQQQ Spike", file=sys.stderr):
            phased = CachedPhasedDay(price_cache, today, schedule)

            # === Phase 1: 09:35 — Settle overnight position ===
            m = phased.resolve_up_to(clock_time(9, 35))
            p0935 = m.get("p0935", {})

            day_ret = 0.0
            if pending is not None:
                sym, entry_price, entry_date = pending
                assert entry_date < today, f"TEMPORAL: {sym} {entry_date} vs {today}"

                exit_price = p0935.get(sym)
                # Settlement fallback: earlier same-day price if exact bar missing
                if exit_price is None:
                    exit_price, resolved_time, resolution = settle_price_fallback(
                        engine, conn, sym, today, "09:35")
                    if exit_price is not None:
                        data_gaps.append({"date": str(today), "symbol": sym,
                            "target": "09:35", "resolved": resolved_time,
                            "resolution": resolution, "price": exit_price})
                        print(f"  GAP: {sym} {today} — {resolution} at {resolved_time}",
                              file=sys.stderr)

                if exit_price and entry_price > 0 and exit_price > 0:
                    raw_ret = exit_price / entry_price - 1
                    day_ret = 0.0 if is_split(raw_ret) else raw_ret - 2 * TC
                n_trades += 1
                if day_ret > 0: n_wins += 1
                elif day_ret < 0: n_losses += 1
                pending = None

            daily_rets.append(day_ret)
            dates.append(today)

            # === Phase 2: 15:30 — Detect VXX spike ===
            aft = phased.resolve_up_to(clock_time(15, 30))
            p1530 = aft.get("p1530", {})

            vxx_0935 = p0935.get("VXX")
            vxx_1530 = p1530.get("VXX")
            vxx_spike = False
            if vxx_0935 and vxx_1530 and vxx_0935 > 0:
                if vxx_1530 / vxx_0935 - 1 > VXX_SPIKE_THR:
                    vxx_spike = True

            # === Phase 3: 16:00 — Enter SQQQ if spike ===
            cl = phased.resolve_up_to(clock_time(16, 0))
            p1600 = cl.get("p1600", {})

            if vxx_spike and pending is None:
                sqqq_price = p1600.get("SQQQ")
                if sqqq_price and sqqq_price > 0:
                    pending = ("SQQQ", sqqq_price, today)

            # SPY benchmark
            spy_prev = prev_p1600.get("SPY")
            spy_curr = p1600.get("SPY")
            if spy_prev and spy_curr and spy_prev > 0:
                spy_ret = spy_curr / spy_prev - 1
                if abs(spy_ret) < SPLIT_THRESHOLD:
                    spy_day_rets[today] = spy_ret

            prev_p1600 = {s: p for s, p in p1600.items() if p is not None}

    finally:
        conn.close()

    # === Bookkeeping ===
    metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
    print_results("07 SQQQ SPIKE", metrics)
    save_results(OUT, "sqqq_spike", daily_rets, dates, metrics, data_gaps)
    plot_pnl(OUT, "SQQQ Spike", daily_rets, dates, trading_days, spy_day_rets,
             metrics, color="#dc2626")


if __name__ == "__main__":
    main()
