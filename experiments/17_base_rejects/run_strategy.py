# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""17 Base Rejects — Second-tier overnight from near-miss stocks.

On days the base strategy has candidates but the top signal fails the 0.74
percentile gate, those near-miss stocks still had positive momentum + high
hit rate. This tests whether entering them with a LOWER gate (or no gate)
captures residual alpha. Fills idle bull-regime nights.

Configs:
  - primary_only: Standard base (baseline comparison)
  - reject_gate50: Enter rejects when signal > 50th percentile
  - reject_gate30: Enter rejects when signal > 30th percentile
  - reject_any: Enter best reject regardless of gate
  - reject_top3: Enter top 3 rejects (diversified)
  - combined_pri_rej50: Always enter — primary if passes, else reject at 50th

Usage:
    python experiments/17_base_rejects/run_strategy.py
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

# Params from exp 166 (same as exp 16)
STREAK_MULT = 0.034
HR_THR = 0.567
LOOKBACK = 68
PRIMARY_PCTILE = 0.74
MIN_IRET = 0.029

CONFIGS = {
    "primary_only":       {"mode": "primary"},
    "reject_gate50":      {"mode": "reject", "reject_pctile": 0.50},
    "reject_gate30":      {"mode": "reject", "reject_pctile": 0.30},
    "reject_any":         {"mode": "reject", "reject_pctile": 0.0},
    "reject_top3":        {"mode": "reject_top3", "reject_pctile": 0.30},
    "combined_pri_rej50": {"mode": "combined", "reject_pctile": 0.50},
}


class Accumulator:
    """Rolling hit-rate and positive-return accumulator."""

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


def run_reject(tape, trading_days, regime_by_day, base_symbols, config_name, cfg,
               *, reject_pctile_override=None):
    """Run one config. Returns dict with metrics + daily_rets + dates.

    If reject_pctile_override is set, overrides cfg's reject_pctile (for Optuna).
    """
    acc = Accumulator(lookback=LOOKBACK)
    signal_history = []

    equity = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    pending = []  # list of (sym, price, date)
    prev_p1530 = {}
    n_primary = 0
    n_reject = 0
    n_wins = 0
    n_losses = 0
    data_gaps = []

    for today in trading_days:
        t = tape.get(today)
        if t is None:
            continue
        p0935 = t["p0935"]
        p1530 = t["p1530"]
        regime = regime_by_day.get(today, "unknown")

        # Update accumulator
        for sym in base_symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        # Settle pending positions
        day_ret = 0.0
        if pending:
            trs = []
            carry = []
            for sym, ep, ed in pending:
                xp = p0935.get(sym)
                if xp and ep > 0 and xp > 0:
                    rr = xp / ep - 1
                    tr = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
                    trs.append(tr)
                    if tr > 0:
                        n_wins += 1
                    elif tr < 0:
                        n_losses += 1
                else:
                    carry.append((sym, ep, ed))
                    data_gaps.append({"date": str(today), "symbol": sym,
                        "target": "09:35", "resolution": "carry_forward"})
                    continue
            if trs:
                day_ret = np.mean(trs)
            pending = carry

        equity *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

        # Build candidates
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
        # NOTE: Reference appends BEFORE percentile check (R1 self-inclusion).
        # Matching reference behavior — documented in TEMPORAL_PROOF.
        if best_sig is not None:
            signal_history.append(best_sig)

        # Primary gate
        primary_passes = False
        if cands and PRIMARY_PCTILE > 0 and len(signal_history) > 60:
            thr = np.percentile(signal_history[-252:], PRIMARY_PCTILE * 100)
            primary_passes = cands[0][0] >= thr
        elif cands and len(signal_history) <= 60:
            primary_passes = True

        # Skip if already holding
        if pending:
            prev_p1530 = {s: p for s, p in p1530.items() if p is not None}
            continue

        reject_pctile = reject_pctile_override if reject_pctile_override is not None else cfg.get("reject_pctile", 0)

        if cfg["mode"] == "primary":
            if primary_passes and cands:
                pending.append((cands[0][1], cands[0][2], today))
                n_primary += 1

        elif cfg["mode"] == "reject":
            if primary_passes and cands:
                pending.append((cands[0][1], cands[0][2], today))
                n_primary += 1
            elif not primary_passes and cands:
                use = True
                if reject_pctile > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], reject_pctile * 100)
                    use = cands[0][0] >= thr
                if use:
                    pending.append((cands[0][1], cands[0][2], today))
                    n_reject += 1

        elif cfg["mode"] == "reject_top3":
            if primary_passes and cands:
                pending.append((cands[0][1], cands[0][2], today))
                n_primary += 1
            elif not primary_passes and cands:
                use_cands = []
                if reject_pctile > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], reject_pctile * 100)
                    use_cands = [(s, sy, p) for s, sy, p in cands[:3] if s >= thr]
                else:
                    use_cands = cands[:3]
                for _, sym, price in use_cands:
                    pending.append((sym, price, today))
                    n_reject += 1

        elif cfg["mode"] == "combined":
            if cands:
                if primary_passes:
                    pending.append((cands[0][1], cands[0][2], today))
                    n_primary += 1
                else:
                    use = True
                    if reject_pctile > 0 and len(signal_history) > 60:
                        thr = np.percentile(signal_history[-252:], reject_pctile * 100)
                        use = cands[0][0] >= thr
                    if use:
                        pending.append((cands[0][1], cands[0][2], today))
                        n_reject += 1

        prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

    dr = np.array(daily_rets)
    dt = pd.to_datetime(dates)
    tts = pd.Timestamp(TRAIN_END)
    n_trades = n_primary + n_reject

    return {
        "train_sh": round(float(sharpe(dr[dt <= tts])), 3),
        "test_sh": round(float(sharpe(dr[dt > tts])), 3),
        "daily_rets": daily_rets, "dates": dates,
        "n_trades": n_trades, "n_wins": n_wins, "n_losses": n_losses,
        "n_primary": n_primary, "n_reject": n_reject, "data_gaps": data_gaps,
    }


def main():
    base_symbols = get_symbols()
    all_symbols = sorted(set(base_symbols + ["SPY", "VXX"]))

    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)

    schedule = build_schedule("exp17_rejects", [
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

    # Pre-compute regime
    regime_model = MacroRegime(fred_panel, min_obs=120, refit_every=20)
    regime_by_day = {}
    for td in tqdm(trading_days, desc="Regime", file=sys.stderr):
        regime_by_day[td] = regime_model.get_regime(td)
    pd.DataFrame([{"date": d, "regime": r} for d, r in regime_by_day.items()]).to_parquet(
        OUT / "regime_cache.parquet", index=False)
    conn.close()

    # Build tape
    tape = {}
    for td in trading_days:
        day_data = price_cache.get(td, {})
        p0935 = {}
        p1530 = {}
        for sym, prices in day_data.items():
            v = prices.get("p0935")
            if v is not None:
                p0935[sym] = v
            v = prices.get("p1530")
            if v is not None:
                p1530[sym] = v
        tape[td] = {"p0935": p0935, "p1530": p1530}

    # SPY B&H benchmark
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
    print(f"  17 — BASE REJECTS (second-tier overnight from near-miss stocks)", file=sys.stderr)
    print(f"{'='*110}", file=sys.stderr)
    print(f"  {'Config':<25s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'Pri':>4s}  {'Rej':>4s}", file=sys.stderr)
    print(f"  {'-'*25}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*4}  {'-'*4}", file=sys.stderr)

    for config_name in tqdm(list(CONFIGS.keys()), desc="Configs", file=sys.stderr):
        cfg = CONFIGS[config_name]
        m = run_reject(tape, trading_days, regime_by_day, base_symbols, config_name, cfg)
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
            "n_primary": m["n_primary"], "n_reject": m["n_reject"],
        }
        print(f"  {config_name:<25s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.02f}  {full_ret:>+7.0f}%  {mdd:>5.1f}%  {wr:>4.0f}%  {m['n_primary']:>4d}  {m['n_reject']:>4d}", file=sys.stderr)

    print(f"{'='*110}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet
    for name, m in config_series.items():
        dr = np.array(m["daily_rets"])
        pd.DataFrame({
            "date": m["dates"], "day_ret": m["daily_rets"],
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{name}.parquet", index=False)

    # Bookkeeping for primary config (reject_gate50 — best train Sharpe)
    primary = "reject_gate50"
    pm = config_series[primary]
    metrics = compute_experiment_metrics(pm["daily_rets"], pm["dates"], pm["n_trades"], pm["n_wins"], pm["n_losses"])
    print_results("17 BASE REJECTS (reject_gate50)", metrics)
    save_results(OUT, primary, pm["daily_rets"], pm["dates"], metrics, pm["data_gaps"])
    plot_pnl(OUT, "Base Rejects (reject_gate50)", pm["daily_rets"], pm["dates"],
             trading_days, spy_day_rets, metrics, color="#dc2626")

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
