# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""16 Adaptive Exit — Adjust exit time based on overnight gap size.

Hypothesis: When overnight gap is large and positive, exit at 09:35 (take
profit). When gap is small/negative, hold until 10:30 (let it develop).
The gap (prev_close → open) is observable at 09:35 — this is causal.

Configs:
  - fixed_0935: Always exit at 09:35 (baseline)
  - fixed_1030: Always exit at 10:30
  - adaptive_positive: Exit 09:35 if gap > 0, else 10:30
  - adaptive_05pct: Exit 09:35 if gap > 0.5%, else 10:30
  - adaptive_1pct: Exit 09:35 if gap > 1%, else 10:30
  - adaptive_median: Exit 09:35 if gap > rolling median, else 10:30

Usage:
    python experiments/16_adaptive_exit/run_strategy.py
"""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict, deque
from datetime import time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE, is_split
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay, Checkpoint,
    ResolutionMode, build_schedule, build_price_cache, load_price_cache,
)
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.metrics import sharpe
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_symbols

OUT = HERE / "output"
SPLIT_THR = SPLIT_THRESHOLD
EXCLUDE = {"SPY", "QQQ", "VXX", "TQQQ"}

# Params from exp 166 (optimized timing, NOT base overnight defaults)
STREAK_MULT = 0.034
HR_THR = 0.567
LOOKBACK = 68
PCTILE = 0.74
MIN_IRET = 0.029

CONFIGS = {
    "fixed_0935":        {"mode": "fixed", "exit_cp": "p0935"},
    "fixed_1030":        {"mode": "fixed", "exit_cp": "p1030"},
    "adaptive_positive": {"mode": "adaptive", "gap_threshold": 0.0},
    "adaptive_05pct":    {"mode": "adaptive", "gap_threshold": 0.005},
    "adaptive_1pct":     {"mode": "adaptive", "gap_threshold": 0.01},
    "adaptive_median":   {"mode": "adaptive_median"},
}


class Accumulator:
    """Rolling hit-rate and positive-return accumulator for overnight momentum."""

    def __init__(self, lookback=68):
        self.lookback = lookback
        self.rets = defaultdict(list)
        self.hit_rate = {}
        self.avg_pos = {}
        self.streak = {}

    def update(self, sym, ret):
        self.rets[sym].append(ret)
        r = self.rets[sym]
        if len(r) < 20:
            self.hit_rate.pop(sym, None)
            self.avg_pos.pop(sym, None)
            self.streak[sym] = 0
            return
        recent = r[-self.lookback:]
        pos = [x for x in recent if x > 0]
        self.hit_rate[sym] = len(pos) / len(recent)
        self.avg_pos[sym] = float(np.mean(pos)) if pos else 0.0
        s = 0
        for x in reversed(r):
            if x > 0:
                s += 1
            else:
                break
        self.streak[sym] = s

    def get_signal(self, sym, iret, streak_mult):
        if sym not in self.hit_rate:
            return None
        return iret * self.avg_pos.get(sym, 0.0) * (1 + streak_mult * self.streak.get(sym, 0)) * self.hit_rate[sym]


def run_adaptive(tape, trading_days, regime_by_day, base_symbols, config_name, cfg,
                 *, gap_threshold_override=None):
    """Run one config. Returns dict with metrics + daily_rets + dates.

    If gap_threshold_override is set, overrides cfg's gap_threshold (for Optuna).
    """
    acc = Accumulator(lookback=LOOKBACK)
    signal_history = []
    gap_history = deque(maxlen=252)

    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    pending = None
    prev_p1530 = {}
    n_trades = 0
    n_wins = 0
    n_losses = 0
    n_early = 0
    n_late = 0
    data_gaps = []

    for today in trading_days:
        t = tape.get(today)
        if t is None:
            continue
        p0935 = t["p0935"]
        p1030 = t["p1030"]
        p1530 = t["p1530"]
        regime = regime_by_day.get(today, "unknown")

        # Phase 1: 09:35 — update accumulator, compute gap, settle early exits
        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        # Compute gap
        gap = None
        if pending:
            sym, ep, ed = pending
            open_price = p0935.get(sym)
            if open_price and ep > 0 and open_price > 0:
                gap = open_price / ep - 1

        # Decide exit timing at 09:35 (R1 fix: median computed BEFORE appending today's gap)
        exit_decision = None
        if not pending:
            exit_decision = None
        elif cfg["mode"] == "fixed" and cfg["exit_cp"] == "p0935":
            exit_decision = "early"
        elif cfg["mode"] == "fixed" and cfg["exit_cp"] == "p1030":
            exit_decision = "late"
        elif cfg["mode"] == "adaptive":
            threshold = gap_threshold_override if gap_threshold_override is not None else cfg["gap_threshold"]
            if gap is not None and gap > threshold:
                exit_decision = "early"
                n_early += 1
            else:
                exit_decision = "late"
                n_late += 1
        elif cfg["mode"] == "adaptive_median":
            med = float(np.median(list(gap_history))) if len(gap_history) > 20 else 0.0
            if gap is not None and gap > med:
                exit_decision = "early"
                n_early += 1
            else:
                exit_decision = "late"
                n_late += 1

        # Append gap AFTER exit decision (R1 fix: avoid self-inclusion)
        if gap is not None and abs(gap) < SPLIT_THR:
            gap_history.append(gap)

        # Settle early exit at 09:35
        day_ret = 0.0
        if exit_decision == "early" and pending:
            sym, ep, ed = pending
            xp = p0935.get(sym)
            if xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                day_ret = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
            else:
                day_ret = -SPLIT_THR - 2 * TC
                data_gaps.append({"date": str(today), "symbol": sym,
                    "target": "09:35", "resolution": "flat_penalty"})
            pending = None
            n_trades += 1
            if day_ret > 0:
                n_wins += 1
            elif day_ret < 0:
                n_losses += 1

        # Phase 2: 10:30 — settle late exits
        if exit_decision == "late" and pending:
            sym, ep, ed = pending
            xp = p1030.get(sym)
            if xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                day_ret = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
            else:
                day_ret = -SPLIT_THR - 2 * TC
                data_gaps.append({"date": str(today), "symbol": sym,
                    "target": "10:30", "resolution": "flat_penalty"})
            pending = None
            n_trades += 1
            if day_ret > 0:
                n_wins += 1
            elif day_ret < 0:
                n_losses += 1

        equity *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

        # Phase 3: 15:30 — entry decision
        cands = []
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
                sig = acc.get_signal(sym, iret, STREAK_MULT)
                if sig is not None:
                    cands.append((sig, sym, p1))
            cands.sort(reverse=True)

        best_sig = cands[0][0] if cands else None

        # Percentile gate (A4a: check THEN append)
        chosen = None
        if cands and PCTILE > 0 and len(signal_history) > 60:
            thr = np.percentile(signal_history[-252:], PCTILE * 100)
            if cands[0][0] >= thr:
                chosen = (cands[0][1], cands[0][2], today)
        elif cands and len(signal_history) <= 60:
            chosen = (cands[0][1], cands[0][2], today)

        if best_sig is not None:
            signal_history.append(best_sig)

        if chosen and pending is None:
            pending = chosen

        prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    tts = pd.Timestamp(TRAIN_END)
    tr_sh = float(sharpe(dr[dt <= tts]))
    te_sh = float(sharpe(dr[dt > tts]))

    return {
        "train_sh": round(tr_sh, 3), "test_sh": round(te_sh, 3),
        "daily_rets": daily_rets, "dates": dates,
        "n_trades": n_trades, "n_wins": n_wins, "n_losses": n_losses,
        "n_early": n_early, "n_late": n_late, "data_gaps": data_gaps,
    }


def main():
    base_symbols = get_symbols()
    all_symbols = sorted(set(base_symbols + ["SPY", "VXX"]))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)

    schedule = build_schedule("exp16_adaptive_exit", [
        Checkpoint(name="p0935", target_time_et=clock_time(9, 35),
                   mode=ResolutionMode.AT_OR_BEFORE,
                   grace_minutes_before=5, grace_minutes_after=0,
                   required=False, trading_day_offset=0),
        Checkpoint(name="p1030", target_time_et=clock_time(10, 30),
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
    # Reference used date.today() — converted to END_DATE for reproducibility.
    trading_days = engine.source.get_trading_days(conn, "2022-01-01", END_DATE)

    cache_path = OUT / "price_cache.parquet"
    if not cache_path.exists():
        print(f"  Building cache: {len(trading_days)} days × {len(all_symbols)} symbols...", file=sys.stderr)
        build_price_cache(engine, conn, trading_days, all_symbols, cache_path)
    price_cache = load_price_cache(cache_path)

    # Pre-compute regime (avoid HMM state mutation across configs)
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)
    regime_by_day = {}
    for td in tqdm(trading_days, desc="Regime", file=sys.stderr):
        regime_by_day[td] = regime_model.get_regime(td)
    pd.DataFrame([{"date": d, "regime": r} for d, r in regime_by_day.items()]).to_parquet(
        OUT / "regime_cache.parquet", index=False)
    conn.close()

    # Build tape from price cache
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        p0935 = {}
        p1030 = {}
        p1530 = {}
        for sym, prices in day_data.items():
            for cp, target in [("p0935", p0935), ("p1030", p1030), ("p1530", p1530)]:
                v = prices.get(cp)
                if v is not None:
                    target[sym] = v
        tape[td] = {"p0935": p0935, "p1030": p1030, "p1530": p1530}

    # SPY B&H benchmark (close-to-close via p1600)
    spy_day_rets = {}
    prev_spy = None
    for td in trading_days:
        spy_p = price_cache.get(td, {}).get("SPY", {}).get("p1600")
        if spy_p and prev_spy and prev_spy > 0:
            r = spy_p / prev_spy - 1
            if abs(r) < SPLIT_THR:
                spy_day_rets[td] = r
        if spy_p:
            prev_spy = spy_p

    # Run all 6 configs
    results = {}
    config_series = {}
    print(f"\n{'='*110}", file=sys.stderr)
    print(f"  16 — ADAPTIVE EXIT TIMING", file=sys.stderr)
    print(f"{'='*110}", file=sys.stderr)
    print(f"  {'Config':<25s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'N':>4s}  {'Early':>5s}  {'Late':>5s}", file=sys.stderr)
    print(f"  {'-'*25}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*4}  {'-'*5}  {'-'*5}", file=sys.stderr)

    for config_name in tqdm(list(CONFIGS.keys()), desc="Configs", file=sys.stderr):
        cfg = CONFIGS[config_name]
        m = run_adaptive(tape, trading_days, regime_by_day, base_symbols, config_name, cfg)
        config_series[config_name] = m

        dr = np.array(m["daily_rets"])
        cum = np.cumprod(1 + dr)
        pk = np.maximum.accumulate(cum)
        mdd = float(abs((cum / pk - 1).min()) * 100)
        full_ret = float((np.prod(1 + dr) - 1) * 100)
        nz = [r for r in dr if r != 0]
        wr = float(np.mean([r > 0 for r in nz]) * 100) if nz else 0

        results[config_name] = {
            "train_sh": m["train_sh"], "test_sh": m["test_sh"],
            "ret": round(full_ret, 1), "dd": round(mdd, 2), "wr": round(wr, 1),
            "n": m["n_trades"], "early": m["n_early"], "late": m["n_late"],
        }
        print(f"  {config_name:<25s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.02f}  {full_ret:>+7.0f}%  {mdd:>5.1f}%  {wr:>4.0f}%  {m['n_trades']:>4d}  {m['n_early']:>5d}  {m['n_late']:>5d}", file=sys.stderr)

    print(f"{'='*110}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for name, m in config_series.items():
        dr = np.array(m["daily_rets"])
        pd.DataFrame({
            "date": m["dates"], "day_ret": m["daily_rets"],
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{name}.parquet", index=False)

    # Bookkeeping for primary config (fixed_0935 — baseline)
    primary = "fixed_0935"
    pm = config_series[primary]
    metrics = compute_experiment_metrics(pm["daily_rets"], pm["dates"], pm["n_trades"], pm["n_wins"], pm["n_losses"])
    print_results("16 ADAPTIVE EXIT (fixed_0935)", metrics)
    save_results(OUT, primary, pm["daily_rets"], pm["dates"], metrics, pm["data_gaps"])
    plot_pnl(OUT, "Adaptive Exit (fixed_0935)", pm["daily_rets"], pm["dates"],
             trading_days, spy_day_rets, metrics, color="#2563eb")

    # Statistical robustness
    from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
    p_dr = np.array(pm["daily_rets"])
    spy_all = np.array([spy_day_rets.get(d, 0.0) for d in pm["dates"]])
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
