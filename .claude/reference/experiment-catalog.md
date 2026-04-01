# Experiment Catalog

Paradigm examples in `experiments/`. Each demonstrates a different strategy pattern. Read these when building a new experiment to find the closest existing pattern.

---

## By Strategy Type

### Single-leg overnight momentum
- **07_sqqq_spike** — VXX spike → SQQQ overnight. Simplest paradigm. Single symbol, single signal.
- **08_gold_overnight** — GLD overnight on gap nights. VXX momentum gate.
- **10_base_overnight** — Cross-sectional stock selection (153 symbols). Accumulator + percentile gate.
- **11_flow_gapfill** — Options flow signal on idle base nights.

### Intraday
- **09_gold_intraday** — NUGT intraday (10:30→16:00). EMA trend signal.
- **15_cross_sectional_gold** — Rank GLD/GDX/NUGT/SLV/SIL by EMA, pick best trending. 8 configs.
- **18_dual_intraday** — Split single intraday into morning + afternoon legs. Tests compounding benefit vs extra TC.

### Cross-sectional
- **12_dynamic_universe** — Top-100 liquid R1000 stocks, re-ranked daily by activity × price.
- **19_gap_reversal** — Mean-reversion vs momentum continuation on overnight gaps. **Original design** (not translation). Null result: reversal fails, inverse is marginal.

### Multi-leg composite
- **14_capital_fix** — 5-leg composite (base + gold ID + gold ON + SQQQ + cash sweep). Tests 6 capital allocation approaches for C2 overlap bug.

### Diagnostic grids
- **13_regime_robustness** — 12 configs testing warmup/breadth/regime filters on 324 OOS symbols. Confirmed overfitting to training universe.
- **16_adaptive_exit** — Exit at 09:35 (take profit) vs 10:30 (develop) based on gap size. First experiment to use `shared/optuna_utils`.
- **17_base_rejects** — Enter near-miss stocks when primary signal fails percentile gate.

### Event-driven
- **20_split_signal** — Buy GLD after inverse ETF reverse splits. ~10 events over 4 years. **Original hypothesis.** Splits mark vol peaks → gold benefits from flight-to-safety unwind. Ensemble (GLD + short VXX + SPY) +42% return. NOT statistically significant — need 20+ years of data.

---

## By Pattern / Infrastructure

| Pattern | Example | Notes |
|---------|---------|-------|
| Pending-row (overnight) | 07, 08, 10, 16, 17 | Entry T, settle T+1 |
| Intraday same-day | 09, 15, 18, 19 | Entry and exit same day, no pending-row |
| Multi-leg DayEquity | 14 | Multiple settlements per day |
| Event-driven | 20 | Binary signal, mostly cash |
| CachedPhasedDay + tape | All (13-20) | build_price_cache → tape dict |
| MacroRegime (HMM) | 13, 14, 16, 17 | Pre-compute regime, save regime_cache.parquet |
| OnlineEMA | 15, 18 | shared/indicators.py |
| Accumulator | 10, 12, 13, 16, 17 | shared/indicators.py or inline |
| Optuna (shared API) | 16, 17, 18 | shared/optuna_utils.run_optimization() |
| Diagnostic grid (skip Optuna) | 13, 14, 15, 19 | Grid IS the parameter search |

---

## Known Bugs Found

| Bug | Experiment | Cataloged |
|-----|-----------|-----------|
| R6: Dict-overwrite fallback | 15 | bug-catalog.md |
| R7: CachedPhasedDay None overwrite | 18 | bug-catalog.md |
| R1: signal_history self-inclusion | 16 (fixed), 17 (documented) | experiment-checks.md |
