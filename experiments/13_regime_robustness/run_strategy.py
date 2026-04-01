# NO FUTURE DATA IS ALLOWED IN THE SYSTEM. All signals from observable data at decision time only.
"""13 — Regime Robustness: diagnose OOS collapse and test regime filters.

The base overnight strategy collapsed on 324 OOS R1000 symbols (Train -0.30,
Test -0.73) while working perfectly on 153 training symbols (Train 2.17,
Test 2.09). The collapse accelerated post-Nov 2024 (election regime change).

Three hypotheses to test:
  1. WARMUP: OOS symbols need more accumulator history before trading
  2. BREADTH: Cross-sectional breadth filters out bad market days
  3. TIGHT REGIME: Stricter bull/bear bounds (0.99-1.01 from exp 62)

Also tests on TRAINING symbols as control to verify filters don't hurt known alpha.

Usage:
    python experiments/13_regime_robustness/run_strategy.py
"""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict
from datetime import date, time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from shared.config import SPLIT_THRESHOLD, TC, TRAIN_END, END_DATE
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, CachedPhasedDay, Checkpoint,
    ResolutionMode, build_schedule, settle_price_fallback,
    build_price_cache, load_price_cache,
)
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl
from shared.metrics import sharpe
from shared.research_core import FRED_PANEL_PATH, INITIAL_CAPITAL, MacroRegime, get_symbols

from common import (
    CONFIGS, EXCLUDE, HR_THR, LB, MIN_IRET, OOS_SYMBOLS,
    PCTILE, SPLIT_THR, STREAK, get_schedule,
)

OUT = HERE / "output"
OUT.mkdir(parents=True, exist_ok=True)


class Accumulator:
    """Accumulator with n_obs tracking for warmup gating."""

    def __init__(self, lookback=80):
        self.lookback = lookback
        self.rets = defaultdict(list)
        self.hit_rate = {}
        self.avg_pos = {}
        self.streak = {}
        self.n_obs = defaultdict(int)

    def update(self, sym, ret):
        self.rets[sym].append(ret)
        self.n_obs[sym] += 1
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


class PartialRegime(MacroRegime):
    """Extends MacroRegime with get_bull_prob() — experiment-specific."""

    def get_bull_prob(self, today):
        from hmmlearn.hmm import GaussianHMM
        h = self.panel.loc[self.panel.index < pd.Timestamp(today)]
        if len(h) < self.min_obs:
            return 0.0
        d = h[self.FEATURE_COLS].dropna().values
        if len(d) < self.min_obs:
            return 0.0
        if self.model is None or (len(d) - self._last_fit_n) >= self.refit_every:
            try:
                m = GaussianHMM(n_components=2, covariance_type="full", n_iter=200, random_state=42)
                m.fit(d)
                si = self.FEATURE_COLS.index("SPY_ret")
                self.bull_state = 0 if m.means_[0][si] > m.means_[1][si] else 1
                self.model = m
                self._last_fit_n = len(d)
            except Exception:
                return 0.0
        if self.model is None or self.bull_state is None:
            return 0.0
        try:
            return float(self.model.predict_proba(d)[-1][self.bull_state])
        except Exception:
            return 0.0


def run_config(tape, regime_cache, bull_prob_cache, trading_days, symbols, *,
               warmup_days, use_breadth, breadth_thr, use_tight_regime, regime_lo, regime_hi,
               name):
    """Run one config through the full strategy loop. Returns (daily_rets, dates, n_trades, data_gaps)."""
    acc = Accumulator(lookback=LB)
    pv = INITIAL_CAPITAL
    daily_rets = []
    dates = []
    pending = None
    prev_p1530 = {}
    signal_history = []
    n_trades = 0
    data_gaps = []

    for today in trading_days:
        td = tape.get(today)
        if td is None:
            continue
        p0935 = td["p0935"]
        p1530 = td["p1530"]
        regime = regime_cache.get(today, "unknown")
        p_bull = bull_prob_cache.get(today, 0.5)

        # Update accumulator with overnight returns
        for sym in symbols:
            if sym in EXCLUDE:
                continue
            pc = prev_p1530.get(sym)
            op = p0935.get(sym)
            if pc and op and pc > 0 and op > 0:
                r = op / pc - 1
                if abs(r) < SPLIT_THR:
                    acc.update(sym, r)

        # Settle pending position
        day_ret = 0.0
        if pending:
            sym, ep, ed = pending
            xp = p0935.get(sym)
            if xp and ep > 0 and xp > 0:
                rr = xp / ep - 1
                day_ret = 0.0 if abs(rr) >= SPLIT_THR else rr - 2 * TC
            else:
                # C-exit: flat penalty (matches reference 174 behavior).
                # settle_price_fallback not available without DB connection.
                day_ret = -SPLIT_THR - 2 * TC
                data_gaps.append({"date": str(today), "symbol": sym,
                    "target": "09:35", "resolution": "flat_penalty",
                    "price": None, "entry_price": ep})
            pending = None
            n_trades += 1

        pv *= (1 + day_ret)
        daily_rets.append(day_ret)
        dates.append(today)

        # Breadth: fraction of symbols with positive intraday return
        breadth = None
        if use_breadth:
            n_pos = 0
            n_total = 0
            for sym in symbols:
                if sym in EXCLUDE:
                    continue
                p0 = p0935.get(sym)
                p1 = p1530.get(sym)
                if p0 and p1 and p0 > 0 and p1 > 0:
                    n_total += 1
                    if p1 / p0 - 1 > 0:
                        n_pos += 1
            breadth = n_pos / n_total if n_total > 20 else None

        # Regime gate
        trade_today = True
        if use_tight_regime:
            trade_today = p_bull >= regime_lo
        else:
            trade_today = regime == "bull"

        if use_breadth and breadth is not None and breadth < breadth_thr:
            trade_today = False

        chosen = None
        best_sig = None
        if trade_today:
            cands = []
            for sym in symbols:
                if sym in EXCLUDE:
                    continue
                if acc.n_obs[sym] < warmup_days:
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
                    cands.append((sig, sym, p1))
            cands.sort(reverse=True)

            if cands:
                best_sig = cands[0][0]
                use = True
                if PCTILE > 0 and len(signal_history) > 60:
                    thr = np.percentile(signal_history[-252:], PCTILE * 100)
                    use = cands[0][0] >= thr
                if use:
                    chosen = (cands[0][1], cands[0][2], today)

        if best_sig is not None:
            signal_history.append(best_sig)
        pending = chosen
        prev_p1530 = {s: p for s, p in p1530.items() if p is not None}

    return daily_rets, dates, n_trades, data_gaps


def main():
    fred_panel = pd.read_parquet(FRED_PANEL_PATH)
    fred_panel.index = pd.to_datetime(fred_panel.index)

    train_symbols = get_symbols()
    all_symbols = sorted(set(OOS_SYMBOLS + train_symbols + ["SPY", "VXX"]))

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
    regime_model = PartialRegime(fred_panel, min_obs=120, refit_every=20)

    # Cache regime + bull_prob per day (same as reference)
    print(f"  Computing regime for {len(trading_days)} days...", file=sys.stderr)
    regime_cache = {}
    bull_prob_cache = {}
    for td in tqdm(trading_days, desc="Regime", file=sys.stderr):
        regime_cache[td] = regime_model.get_regime(td)
        bull_prob_cache[td] = regime_model.get_bull_prob(td)
    conn.close()

    # Build tape from price cache (same structure as reference)
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

    # SPY B&H benchmark (close-to-close via p1600)
    spy_day_rets = {}
    prev_spy = None
    for td in trading_days:
        day_data = price_cache.get(td, {})
        spy_p = day_data.get("SPY", {}).get("p1600") if day_data.get("SPY") else None
        if spy_p and prev_spy and prev_spy > 0:
            r = spy_p / prev_spy - 1
            if abs(r) < SPLIT_THR:
                spy_day_rets[td] = r
        if spy_p:
            prev_spy = spy_p

    # Resolve symbol sets
    symbol_map = {"oos": OOS_SYMBOLS, "train": train_symbols}

    # Run all 12 configs
    results = {}
    config_series = {}  # store (rets, dates, n_trades) per config
    print(f"\n  Running {len(CONFIGS)} configs...", file=sys.stderr)
    for config_name, cfg in tqdm(CONFIGS.items(), desc="Configs", file=sys.stderr):
        syms = symbol_map[cfg["symbols"]]
        rets, dts, nt, gaps = run_config(
            tape, regime_cache, bull_prob_cache, trading_days, syms,
            warmup_days=cfg["warmup_days"], use_breadth=cfg["use_breadth"],
            breadth_thr=cfg["breadth_thr"], use_tight_regime=cfg["use_tight_regime"],
            regime_lo=cfg["regime_lo"], regime_hi=cfg["regime_hi"],
            name=config_name,
        )
        config_series[config_name] = (rets, dts, nt, gaps)
        dr = np.array(rets)
        dt = pd.to_datetime(dts)
        tts = pd.Timestamp(TRAIN_END)
        tr, te = dr[dt <= tts], dr[dt > tts]
        cum = np.cumprod(1 + dr)
        pk = np.maximum.accumulate(cum)
        mdd = float(abs((cum / pk - 1).min()) * 100)
        full_ret = float((np.prod(1 + dr) - 1) * 100)
        nz = [r for r in dr if r != 0]
        wr = float(np.mean([r > 0 for r in nz]) * 100) if nz else 0
        results[config_name] = {
            "train_sh": round(float(sharpe(tr)), 3),
            "test_sh": round(float(sharpe(te)), 3),
            "ret": round(full_ret, 1),
            "dd": round(mdd, 2),
            "wr": round(wr, 1),
            "n": nt,
        }

    # Print summary
    print(f"\n{'='*110}", file=sys.stderr)
    print(f"  13 — REGIME ROBUSTNESS: OOS diagnosis + regime filters", file=sys.stderr)
    print(f"{'='*110}", file=sys.stderr)
    print(f"  {'Config':<25s}  {'Tr Sh':>6s}  {'Te Sh':>6s}  {'Ret':>8s}  {'DD':>6s}  {'WR':>5s}  {'N':>5s}", file=sys.stderr)
    print(f"  {'-'*25}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*5}", file=sys.stderr)

    for section, prefix in [("OOS (324 symbols)", "oos_"), ("TRAINING CONTROL (153)", "train_")]:
        print(f"\n  --- {section} ---", file=sys.stderr)
        for cname, m in sorted(results.items(), key=lambda x: min(x[1]["train_sh"], x[1]["test_sh"]), reverse=True):
            if not cname.startswith(prefix):
                continue
            print(f"  {cname:<25s}  {m['train_sh']:>+5.2f}  {m['test_sh']:>+5.02f}  {m['ret']:>+7.0f}%  {m['dd']:>5.1f}%  {m['wr']:>4.0f}%  {m['n']:>5d}", file=sys.stderr)

    print(f"{'='*110}", file=sys.stderr)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    # Save per-config parquet for verification
    for config_name, (rets, dts, nt, _gaps) in config_series.items():
        dr = np.array(rets)
        pd.DataFrame({
            "date": dts,
            "day_ret": rets,
            "equity": INITIAL_CAPITAL * np.cumprod(1 + dr),
        }).to_parquet(OUT / f"{config_name}.parquet", index=False)

    # Bookkeeping for primary config (train_baseline — positive control)
    primary = "train_baseline"
    p_rets, p_dates, p_nt, p_gaps = config_series[primary]
    p_dr = np.array(p_rets)
    p_nz = [r for r in p_dr if r != 0]
    p_nw = sum(1 for r in p_nz if r > 0)
    p_nl = sum(1 for r in p_nz if r < 0)
    metrics = compute_experiment_metrics(p_rets, p_dates, p_nt, p_nw, p_nl)
    print_results("13 REGIME ROBUSTNESS (train_baseline)", metrics)
    save_results(OUT, primary, p_rets, p_dates, metrics, p_gaps)
    plot_pnl(OUT, "Regime Robustness (train_baseline)", p_rets, p_dates,
             trading_days, spy_day_rets, metrics, color="#dc2626")

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
