# Statistical Robustness Analysis

Mandatory guide for experiment agents, reviewers, and adversaries. The 3 minimum tests (permutation, bootstrap CI, concentration) must be run and reported in TEMPORAL_PROOF.md. Results do not need to be favorable — the gate requires running and reporting, not passing.

---

## Purpose

Statistical analysis serves two roles in this project, in priority order:

1. **Bug detection.** A permutation test that shows p=0.80 is stronger evidence of a bug than any code review. Statistical tests catch structural biases that pass all 7 verification checks — the signal "works" but only because the experiment design guarantees it.

2. **Signal evaluation.** Once bugs are ruled out, statistical tests characterize the signal: is it robust, fragile, regime-dependent, concentrated in outliers?

Never skip to (2). A beautiful IC decay curve means nothing if the signal has a timezone bug.

---

## Starter Toolkit (`shared/stat_tests.py`)

Six functions that cover common cases. Import what you need — no classes, no state. Each returns a dict with `test_name`, `result`, `interpretation`, and `pass` keys.

### Bug Detectors

| Function | What it answers | When to use |
|----------|----------------|-------------|
| `permutation_test` | Could random day selection produce this Sharpe? | Every experiment. The single most informative bug detector. |
| `bootstrap_sharpe_ci` | Is the Sharpe distinguishable from zero? | Every experiment. Fast, always useful. |
| `concentration_ratio` | Do a few outlier days drive the entire result? | Every experiment. Especially important for event-driven strategies. |
| `return_autocorrelation` | Are returns serially correlated (inflating Sharpe)? | When Sharpe > 1.0, or when the strategy has multi-day holds. |

### Signal Quality

| Function | What it answers | When to use |
|----------|----------------|-------------|
| `ic_series` | Does the signal rank-predict forward returns? | Cross-sectional signals (ranking assets by signal strength). |
| `ic_decay` | Does predictive power fade with lag? | When IC is meaningful. Real signals decay; bugs don't. |
| `regime_stability` | Does the signal work in both bull and bear? | Regime-conditioned strategies. |

### Usage Pattern

```python
from shared.stat_tests import permutation_test, bootstrap_sharpe_ci, concentration_ratio

# signal_returns: full daily return series (0.0 on flat days)
# benchmark_returns: buy-and-hold daily returns for same period

perm = permutation_test(signal_returns, benchmark_returns)
boot = bootstrap_sharpe_ci(signal_returns)
conc = concentration_ratio(signal_returns)

# Log results for TEMPORAL_PROOF.md
for test in [perm, boot, conc]:
    print(f"{test['test_name']}: {test['result']} — {test['interpretation']}")
```

### Runtime Considerations

`permutation_test` runtime = `n_perms × cost_of_sharpe_on_N_days`. For typical experiments (1000 days, 1000 perms), this is sub-second. If your strategy involves expensive computation per permutation (e.g., HMM refit), either:
- Shuffle only the signal-to-return mapping (not the full strategy recomputation)
- Reduce `n_perms` to 200-500
- Document why fewer perms were used

All other functions are O(N) or O(N × max_lag) — negligible runtime.

---

## Decision Framework

Not every experiment needs every test. Use judgment based on what the experiment does.

### Minimum for every experiment
- `permutation_test` — is the signal distinguishable from random?
- `bootstrap_sharpe_ci` — is the Sharpe distinguishable from zero?
- `concentration_ratio` — is the result driven by outliers?

### Additional tests by experiment type

**Overnight long/flat signal:**
- `return_autocorrelation` — multi-day streaks inflate Sharpe
- Compare signal-on mean vs unconditional mean (already in Check 7)

**Cross-sectional ranking:**
- `ic_series` + `ic_decay` — rank prediction is the core claim
- Concentration by asset (custom: does one ticker drive the result?)

**Event-driven (earnings, macro):**
- Event clustering test (custom: are profitable events clustered in time?)
- Single-event knockout (custom: remove each event, does result survive?)
- Concentration is critical — event strategies often have few trades

**Regime-switching:**
- `regime_stability` — does the signal work in both regimes?
- Regime boundary sensitivity (custom: shift labels ±N days, check stability)

**Multi-asset portfolio:**
- Single-asset knockout (custom: remove each asset, does result survive?)
- Cross-asset correlation of returns (custom: are the diversification benefits real?)

### Academic benchmark comparison

If PREFLIGHT.md records statistical methodology from academic papers (see experiment-pipeline STEP 0), compare the experiment's results against those benchmarks:

- **Effect size:** Is the observed Sharpe/IC within the range reported in the literature? Significantly higher warrants investigation — the paper used cleaner data, longer history, or different methodology.
- **Test selection:** If the paper used Newey-West standard errors for autocorrelation, consider whether `return_autocorrelation` should be mandatory (not optional) for your experiment.
- **Sample size:** If the paper used 50 years of data and your experiment uses 4, note the statistical power difference. Wide bootstrap CIs on short samples are expected, not alarming.
- **Methodology notes:** Record any statistical techniques from the papers that could be applied to your results. These inform future toolkit additions.

This is documentation, not a gate. The purpose is to calibrate expectations and catch anomalies early.

---

## Writing Custom Tests

The starter toolkit covers common patterns. When your experiment needs something specific, write a custom test function following this contract:

1. **Pure function.** numpy/scipy arrays in, dict out. No DB, no file I/O.
2. **Structured output.** Return a dict with at minimum: `test_name`, `result`, `interpretation`, `pass`.
3. **Self-contained.** The function should work given only the data arrays it receives.
4. **Documented threshold.** If the test has a pass/fail threshold, document why that threshold was chosen.

Example — event clustering test for an earnings strategy:

```python
def event_clustering(
    event_dates: np.ndarray,  # ordinal dates of profitable events
    all_dates: np.ndarray,    # all trading dates in the period
    max_gap_days: int = 5,
) -> dict:
    """Are profitable events clustered in time?

    If yes, the 'signal' may be a regime artifact — a stretch of favorable
    market conditions that happened to coincide with events.
    """
    # ... implementation ...
    return {
        "test_name": "event_clustering",
        "n_clusters": n_clusters,
        "largest_cluster_pct": largest / len(event_dates),
        "result": "clustered" if largest / len(event_dates) > 0.3 else "distributed",
        "interpretation": "...",
        "pass": largest / len(event_dates) <= 0.3,
    }
```

Put custom test functions in the experiment's own root directory (e.g., `experiments/H9/stat_tests.py`). Write test output (JSON results, plots) to the experiment's `output/` directory — never to `shared/` or the repo root. If a custom test proves broadly useful across experiments, propose moving it to `shared/stat_tests.py` in the iteration patch.

---

## Interpreting Results

### Permutation test
- **p < 0.05:** Signal timing is better than random. Not proof of alpha — could still be regime capture — but the selection mechanism adds value.
- **p > 0.20:** Signal timing is indistinguishable from random. The "alpha" is structural bias, market direction, or luck. Investigate before accepting the result.
- **perm_mean > observed:** Random day selection produces HIGHER Sharpe than the signal. The signal is selecting WORSE days than chance — it may be actively harmful, or the strategy's returns come entirely from market direction on active days (60%+ active rate means the signal is barely filtering).
- **0.05 < p < 0.20:** Gray zone. Report honestly. More data or a different test may resolve it.

### Bootstrap Sharpe CI
- **CI excludes zero:** Sharpe is statistically meaningful at the chosen confidence level.
- **CI includes zero:** The observed Sharpe could plausibly be zero. Does not mean the signal is worthless — may mean the sample is too small.
- **CI is very wide** (e.g., [-0.5, 2.0]): High uncertainty. The point estimate is unreliable.

### Concentration
- **Top 5 days < 30% of P&L:** Well-distributed. Robust.
- **Top 5 days > 50% of P&L:** Fragile. Investigate whether top days are data artifacts, splits, or outlier market events.
- **Sign flips without top N days:** The result literally depends on a handful of observations. Very fragile.

### Autocorrelation
- **No significant lags:** Returns appear independent. Sharpe is not inflated by serial correlation.
- **Positive lag-1:** Sharpe is inflated by the reported factor. Common in multi-day holds or momentum signals.
- **Negative autocorrelation:** Does NOT inflate Sharpe — it deflates it (mean-reversion). Flagged as "autocorrelated" for awareness but is not a concern for Sharpe reliability.
- **Significant higher lags:** Suggests weekly or monthly periodicity — investigate the source.

### IC / IC decay
- **IC > 0.05, p < 0.05:** Practically and statistically meaningful predictive power.
- **IC monotonically decays with lag:** Consistent with real signal. The information gets priced in over time.
- **IC flat or increasing with lag:** Suspicious. Real predictive power should weaken at longer horizons. Flat IC suggests look-ahead bias or structural artifact.

### Regime stability
- **Consistent direction across regimes:** Signal is not just regime capture. Stronger evidence of a real edge.
- **Works in one regime only:** May be capturing regime direction rather than a distinct signal. Not necessarily wrong — some signals are regime-dependent by hypothesis — but must be documented.

---

## Anti-Patterns

**Running 20 tests and reporting the 3 that pass.** This is p-hacking. Report all tests you ran, not just the favorable ones. If you ran 20 tests, the significance threshold is ~0.0025 (Bonferroni), not 0.05.

**Using statistical significance to override structural bugs.** A low p-value on a permutation test does not fix a timezone error. Statistical tests complement the 7-step verification, they don't replace it.

**Permutation test on a strategy with path-dependent state.** If the strategy maintains state across days (e.g., rolling averages, streak counters), shuffling individual day returns breaks the state dependency. Shuffle contiguous blocks instead, or shuffle the signal-to-return mapping while preserving signal structure.

**Trusting IC on a time-series long/flat strategy.** IC (rank correlation) is designed for cross-sectional signals where you rank multiple assets each period. For a single-asset long/flat signal, the permutation test and Check 7 spread analysis are more informative.

**Overfitting the threshold.** If you adjust the pass/fail threshold on a custom test until your experiment passes, you haven't tested anything. Set thresholds before seeing results, or use conventional values (p < 0.05, IC > 0.05).

---

## Evolution

This toolkit and guide evolve through the iteration loop, just like the rules and infrastructure.

### How it improves

Each iteration cycle can produce statistical analysis patches from three sources:

1. **Experiment agent** writes custom tests for its specific experiment. If the custom test reveals something broadly useful, it's a candidate for `shared/stat_tests.py`.

2. **Reviewer** evaluates the experiment's statistical analysis choices (Section C of the review). The reviewer proposes: tests the experiment should have run, new functions for the starter toolkit, and updates to this decision framework.

3. **Adversary** may use statistical analysis to break the experiment — or may find that a statistical test the experiment relied on was misleading. Either finding can produce a patch.

### What the orchestrator patches

After each cycle, the orchestrator evaluates statistical analysis proposals alongside rule patches:

- **`shared/stat_tests.py`** — new functions that proved useful across experiments, improvements to existing functions (better defaults, edge case handling, clearer output).
- **This document** — new experiment types in the decision framework, new anti-patterns discovered, updated interpretation guidance based on what actually confused agents.
- **Experiment-specific tests that DON'T generalize** stay in the experiment directory. Not everything belongs in shared/.

### Promotion criteria

A custom test moves from an experiment directory to `shared/stat_tests.py` when:
- It was useful in 2+ experiments (or would have been, per reviewer assessment)
- It follows the contract (pure function, structured dict output)
- It uses only numpy/scipy (no new dependencies)
- It addresses a failure mode that the existing toolkit misses

### What NOT to add

- Tests that only apply to one signal type or instrument
- Tests that duplicate existing functions with different defaults
- Tests that require database access or file I/O
- Wrappers around scipy functions that don't add meaningful interpretation
