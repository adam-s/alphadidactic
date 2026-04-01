---
name: experiment-pipeline
description: The 5-step build pipeline (PRE-FLIGHT, DATA, STRATEGY, OPTIMIZE, file structure). Use when creating a new experiment — pre-flight documentation, building datasets, implementing strategies.
user-invocable: false
---

# Experiment Pipeline — Build Steps

Steps 0, 1, 2, and 5 of the experiment protocol. These are needed by the experiment agent during construction. Verification and reporting criteria are in `rules/experiment-checks.md`.

---

## STEP 0: PRE-FLIGHT

Before writing any code:

### 0a. Web search (MANDATORY — use the WebSearch tool)

You MUST call the WebSearch tool at least once before writing any code. Do NOT skip this step. Do NOT write literature references from memory — your training knowledge may be hallucinated.

Call WebSearch with a query specific to the hypothesis being tested. Examples:
- `"[your hypothesis] academic paper out of sample"`
- `"[your signal type] alpha decay replication"`
- `"[your instrument class] [your strategy type] empirical evidence"`

One focused, hypothesis-specific search is sufficient. Additional searches are optional if the first result is inconclusive or the hypothesis spans multiple domains.

Paste the actual search results into PREFLIGHT.md under "## Literature Grounding". Include URLs.

**Statistical methodology note:** When reading papers in the search results, note which statistical tests they use (e.g., Newey-West t-stats, bootstrap, Fama-MacBeth regressions), their sample sizes, and reported effect sizes (Sharpe, IC, t-stat). Record these alongside the URLs. This calibrates the expected Sharpe range (item 7) and guides which additional tests from `reference/statistical-analysis.md` to run in STEP 2b.

### 0b. Pre-flight documentation

Document:

1. **Hypothesis:** What causal relationship are you testing?
2. **Signal type:** Price-based, volume-based, options flow, macro, cross-asset
3. **Return target:** Exactly which return are you earning? State the formula explicitly (e.g., `p0935[T+1]/p1600[T] - 1` for pure overnight, `p1600[T+1]/p1600[T] - 1` for close-to-close). "Overnight return" is ambiguous — define it precisely before writing code.
4. **Data sources:** Which tables, which columns, which time range
5. **Temporal availability:** When is each data point available in wall-clock time?
6. **Split risk assessment:** Does this signal/return involve any split-adjusted data?
7. **Expected Sharpe range:** What range would be plausible? (> 3.0 in test triggers mandatory investigation). If results exceed the documented upper warning bound, the report must include: (a) comparison against the academic benchmark that motivated the bound, (b) a confidence interval on the Sharpe showing whether the result is within sampling noise, and (c) the specific regime or sample characteristic that explains the excess. Narrative explanations alone are insufficient.
8. **Date range:** Use `START_DATE` and `END_DATE` from `shared/config.py` as defaults. `TRAIN_END` is the train/test split boundary, not the experiment end date. If the implemented date range differs from the hypothesis or config, document the reason explicitly — a narrowed date range changes statistical power and may affect whether the hypothesis can be tested as intended.
9. **Partial-period boundary check:** If the signal references calendar periods (month, week, quarter), verify that the first and last periods in the date range are complete. Either exclude partial boundary periods or compute period boundaries from the full calendar (not the dataset's trading days).

**GATE:** WebSearch tool called at least once with results pasted into PREFLIGHT.md (with URLs). Pre-flight completed with all 9 items. Return target formula explicitly stated. No code written yet.

---

## STEP 1: DATA (build dataset)

Build the dataset using `build_dataset.py`.

### Mandatory constraints:
- Before writing ANY code, read `reference/experiment-catalog.md` to find the closest existing paradigm experiment. Follow its structure.
- Before writing ANY code, `ls shared/` and check if a function already exists for what you need. Use existing shared modules instead of reimplementing.
- Use shared infrastructure for ALL external data access (DB, FRED, options). Never write raw SQL.
- ALL hypertable queries must have single-day time bounds (see `rules/database-safety.md` for why).
- Check symbol density tier before querying: `shared/db_monitor.get_density(symbol)`.
- Run `python -m shared.system_monitor` before starting. STOP if any resource is CRITICAL.

### Universe construction must be point-in-time:
Any data-derived filter on the symbol universe (liquidity, volume, market cap, flow, sector membership) must use only data available at the time of each trading day — not full-period aggregates. Either (1) use a structurally defined universe that doesn't depend on realized data, (2) apply the filter with a rolling lookback using only prior data, or (3) document the full-period filter as a known limitation with impact analysis.

### Test small first — with a time budget:
Before running the full date range, test on 5 trading days with the full symbol universe. Measure wall time per day. Extrapolate: `estimated_total = (time_per_day) * total_days`. If estimated total exceeds 20 minutes, STOP and optimize before expanding.

When optimizing, look for N×M query patterns and collapse them into M queries. Verify the optimized path produces identical results to the original on the 5-day test set before expanding. Every change must prove equivalence.

**Optimize the query, not the data source.** Pre-aggregated or summarized tables lose information that the authoritative source preserves. If a different data source is faster, it must prove equivalence on the metric the strategy uses — exact match on 20+ test cases.

### Hard stops:
- Raw SQL without day bounds → STOP, use temporal_sources
- `DISTINCT ON` across multi-day range → STOP, use per-day query
- `time::time` without dual timezone conversion → STOP, use cursor_engine

**GATE:** Parquet + manifest produced. All queries single-day bounded.

---

## STEP 2: STRATEGY (implement)

Implement the strategy in `run_strategy.py`.

### Mandatory constraints:
- Use the pending-row pattern for ALL return pairing (see `reference/pending-row-pattern.md`)
- Respect TRAIN_END split (from `shared/config.py` or experiment config)
- Apply split filters on BOTH signal and return sides
- Transaction costs from `shared/config.py`
- Multiplicative equity compounding

### Pattern:
```python
from shared.config import TC, TRAIN_END

prev_decision = None
equity = 1.0
results = []

for T in trading_dates:
    ovn_ret = p0935[T] / p1552[T-1] - 1

    if prev_decision == True:  # noqa: E712  (numpy.bool_ safe)
        day_ret = ovn_ret - 2 * TC
        equity *= (1 + day_ret)

    # Compute today's signal using only data available at decision time
    signal_value = compute_signal(data_up_to[T])
    prev_decision = signal_value > threshold

    results.append({'date': T, 'equity': equity, 'signal': signal_value})
```

### Signal sanity check (before full run):
- **Cross-sectional strategies:** `std(signal) < 1e-10` = degenerate by construction
- **Ratio signals:** If signal is a ratio (A / (A + B)), check that both numerator and denominator are non-trivial. A ratio where most values cluster at 0.0 or 1.0 is effectively binary — ranking is tie-breaking noise. Require a minimum activity threshold on the denominator (e.g., total flow > $1M) to exclude symbols with negligible activity.
- **Time-series strategies:** Signal must take both True and False values

### Rolling window self-inclusion check:
If the signal uses a rolling z-score, percentile, or rank, verify the computation excludes the current observation from its own standardization window.

### Event-strategy holding period check:
For event-driven strategies, verify that the holding period actually captures the intended event. If > 20% of trades exit before the event, the strategy does not test the stated hypothesis.

### Hard stops:
- `signal[T]` paired with `return[T]` → STOP, use pending-row
- Sharpe > 3.0 in test (full period OR any sub-period ≥ 6 months) → STOP, investigate
- No split filter → STOP, add split adjustment on both sides
- Signal cross-sectional std < 1e-10 → STOP, signal is degenerate by construction
- Ratio signal where > 50% of values are at boundary (0.0 or 1.0) → STOP, signal is effectively binary; add minimum activity threshold on denominator
- Vectorized signal/return computation in `run_strategy.py` → STOP, use day-by-day loop
- Position sizing from future data → STOP, size from information available at entry time (see `rules/accounting-correctness.md` § C-sizing)
- `date.today()` in date range → STOP, use `END_DATE` from `shared/config.py`. `date.today()` makes results non-reproducible across runs

**GATE:** No forward-looking data access. Pending-row pattern used. Split filters on both sides. Strategy loop iterates day-by-day.

### STEP 2b: Statistical Robustness (MANDATORY)

After `run_strategy.py` produces results, run the 3 minimum statistical tests:

```python
from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio
import pandas as pd

results = pd.read_parquet("output/results.parquet")
signal_returns = results["day_ret"].values
# benchmark_returns: buy-and-hold returns for same instrument/period

perm = permutation_test(signal_returns, benchmark_returns)
boot = bootstrap_sharpe_ci(signal_returns)
conc = concentration_ratio(signal_returns)

for test in [perm, boot, conc]:
    print(f"{test['test_name']}: {test['result']} — {test['interpretation']}")
```

Report results in TEMPORAL_PROOF.md under "## Statistical Robustness". See `reference/statistical-analysis.md` for additional tests by experiment type.

### Bookkeeping (use shared/experiment_results.py)

After the strategy loop, use the shared bookkeeping functions — do not reimplement metrics or plotting:

```python
from shared.experiment_results import compute_experiment_metrics, print_results, save_results, plot_pnl

metrics = compute_experiment_metrics(daily_rets, dates, n_trades, n_wins, n_losses)
print_results("EXPERIMENT NAME", metrics)
save_results(OUT, "leg_name", daily_rets, dates, metrics, data_gaps)
plot_pnl(OUT, "Title", daily_rets, dates, trading_days, spy_day_rets, metrics, color="#2563eb")
```

The P&L chart includes SPY buy-and-hold benchmark automatically.

---

## STEP 5: OPTIMIZE (after correctness is confirmed)

Optimization is a separate phase that runs AFTER the experiment passes all correctness checks (STEP 3). There are two types: performance optimization (speed) and parameter optimization (Optuna). Both require verified correctness as a precondition.

### 5a. Performance optimization

Profile first, then optimize the actual bottleneck:

1. **Profile** — `python -m shared.profiler run EXPERIMENT_DIR/build_dataset.py`
2. **Analyze** — Read `PERFORMANCE.md`. Where's the bottleneck?
3. **Optimize** — Apply ONE change. Don't change multiple things at once.
4. **Verify** — Re-run correctness checks (STEP 3). Optimization must not break results.
5. **Measure** — Compare `PERFORMANCE.md` before and after.

### 5b. Parameter optimization (Optuna) — MANDATORY

**ALWAYS run Optuna after STEP 3 passes.** A null result at one parameterization tells you nothing about the hypothesis — the hardcoded parameters might be far from where the signal lives. An EMA with a 1000-day window will show no signal; the same EMA at 16 days might be strong. Optuna systematically searches the parameter space. If 200 trials across reasonable ranges all produce null, that is the definitive null result — not a single point estimate.

**Exception: diagnostic grid experiments.** If the experiment's purpose is an exhaustive multi-config comparison (e.g., testing warmup/regime/filter variants), the grid IS the parameter search. Skip Optuna and document in TEMPORAL_PROOF.md why the grid is sufficient.

Use `shared/optuna_utils.py` for all parameter optimization. The workflow:

1. **Baseline first.** The experiment MUST work with hardcoded parameters and pass all 7 checks BEFORE Optuna runs. Optuna on a buggy strategy finds parameters that exploit the bug.
2. **Train-only objective.** The objective function MUST use only train-period metrics. Test data is holdout — logged for gap analysis but NEVER used for parameter selection.
3. **Seed with baseline.** Always enqueue the hardcoded baseline params as the first trial. Optuna can't do worse than the baseline.
4. **Trial cap.** 150-200 trials. Hard ceiling 200 unless documented justification. More trials = more overfitting risk for diminishing returns.
5. **Re-verify.** After optimization, re-run STEP 3 with the optimized parameters. All 7 checks must still pass.
6. **Report both.** TEMPORAL_PROOF must report baseline AND optimized results side-by-side, with train-test gap analysis.

See `shared/optuna_utils.py` for the API.

**Overfitting frontier:** The best parameters are NOT necessarily the highest train metric. Use `shared/optuna_utils.analyze_overfitting()` to find the Pareto frontier — high train performance with small train-test gap.

**GATE:** Optimized parameters must pass all STEP 3 checks. Baseline vs optimized comparison reported. Train-test gap analyzed.

---

## File Structure per Experiment

```
experiments/XX_experiment_name/
├── common.py               # Experiment-specific config and helpers
├── build_dataset.py        # STEP 1: Data collection
├── run_strategy.py         # STEP 2: Strategy implementation
├── verify_integrity.py     # STEP 3: 7-step verification
├── run_optuna.py           # STEP 5b: Parameter optimization (if needed)
├── stat_tests.py           # Custom statistical tests (if needed)
├── TEMPORAL_PROOF.md       # STEP 4: Complete report
├── PREFLIGHT.md            # STEP 0: Pre-flight document
├── PROGRESS.md             # Agent progress updates (written during execution)
├── PERFORMANCE.md          # Performance profile (written by profiler)
└── output/                 # ALL generated artifacts — nothing generated goes elsewhere
    ├── dataset.parquet     # Cached dataset
    ├── results.parquet     # Strategy results
    ├── pnl_chart.png       # P&L visualization
    ├── stat_results.json   # Statistical robustness results
    ├── optuna_results.json # Full optimization results (if STEP 5b ran)
    └── optuna_log.csv      # Per-trial log for analysis (if STEP 5b ran)
```

**All generated files** (parquet, CSV, plots, JSON, `__pycache__`, profiler output) go in `output/`. Source code (`.py`) and reports (`.md`) stay in the experiment root. Never write generated artifacts to `shared/`, the repo root, or other experiments.
