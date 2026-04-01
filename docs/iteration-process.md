# Iteration Process: Develop .claude/ by Recreating Experiments

## Goal

Improve `.claude/` and `shared/` infrastructure by recreating single-legged experiments from `reference_experiments/` (160-195). Each iteration tests whether the instruction set + infrastructure can produce a correct experiment. Failures expose gaps. Gaps get fixed immediately — no backward compatibility, no hesitation.

## Experiment Queue (later = more refined, start here)

Working backward from 195. Skip composites and multi-leg for now.

| Priority | Experiment | Signal Type | Why This Tests .claude/ |
|----------|-----------|-------------|------------------------|
| 1 | 175_dynamic_universe | Dynamic liquidity filter | Tests point-in-time universe construction |
| 2 | 174_regime_robustness | Regime filters | Tests MacroRegime, OOS analysis |
| 3 | 173_composite_tuned | OOS validation (324 new symbols) | Tests universe expansion, cache rebuilding |
| 4 | 172_adaptive_exit | Variable exit timing | Tests non-standard checkpoint schedules |
| 5 | 171_base_rejects | Second-tier stock selection | Tests cross-sectional ranking variations |
| 6 | 170_cross_sectional_gold | Gold sector ranking | Tests multi-instrument cross-section |
| 7 | 169_dual_intraday_checkpoint | AM + PM compounding | Tests multi-settlement per day |
| 8 | 168_options_flow_signal | Flow as filter on base | Tests flow_cache integration with base |
| 9 | 167_optimized_timing | Tuned entry/exit times | Tests Optuna + custom checkpoints |
| 10 | 166_entry_exit_timing | Entry/exit sweep | Tests parameter sweep methodology |

## Iteration Cycle (per experiment)

### Step 1: READ the reference

Read the reference experiment's `run_strategy.py`. Understand:
- What signal does it compute?
- What data does it need?
- What checkpoints does it use?
- How does it differ from the paradigm examples (07-11)?

### Step 2: BUILD from .claude/ instructions + shared/

Create the experiment using ONLY:
- `.claude/` rules, skills, reference docs
- `shared/` infrastructure
- The paradigm examples (07-11) as templates

Do NOT copy from the reference. The point is to test whether `.claude/` produces correct code.

### Step 3: RUN and compare

Run the new experiment. Compare against the reference:
- Sharpe should be in the same ballpark (exact match unlikely due to parameter differences)
- Trade count should be similar
- Signal direction should match

### Step 4: VERIFY

Run `verify_integrity.py` (8 checks). Run reviewer + adversary agents.

### Step 5: DIAGNOSE failures

When something goes wrong (it will), diagnose:
1. **Is it a .claude/ instruction gap?** → Patch the instruction (update existing, don't add new)
2. **Is it a shared/ infrastructure bug?** → Fix immediately, re-run ALL paradigm experiments to verify no regression
3. **Is it a missing shared/ function?** → Add it, document in shared-infrastructure-guide.md
4. **Is it an experiment-specific issue?** → Fix in the experiment, not in .claude/

### Step 6: FIX and re-run

Fix the root cause. **No backward compatibility** — if fixing a shared module changes Sharpe on existing experiments, re-run all experiments and update baselines.

Then re-run the SAME experiment to verify the fix worked.

### Step 7: COMMIT

One commit per fix. Commit message describes what broke and what the fix generalizes to.

## Rules

1. **Fix the infrastructure, not the experiment.** If the experiment agent would need a hack to make it work, the infrastructure is wrong.
2. **Re-run everything after infrastructure changes.** A fix to `shared/cursor_engine.py` affects all experiments. Verify with `for exp in experiments/*/run_strategy.py; do python "$exp" 2>&1 | grep "Train Sharpe"; done`.
3. **Delete freely.** If a shared function is wrong, delete it and write a correct one. Update every caller.
4. **The paradigm examples are the test suite.** After any infrastructure change, all 5 must produce their baseline Sharpe. If they don't, the change is wrong.
5. **One experiment at a time.** Don't start the next until the current one passes reviewer + adversary.

## Tracking

Update this table after each iteration:

| # | Experiment | Status | .claude/ patches | shared/ changes | Baseline verified |
|---|-----------|--------|-----------------|-----------------|-------------------|
| 1 | 175_dynamic_universe | DONE | Check 1 (10+ syms for large), Check 3 (no tautology), Check 4 (replay for cross-section), Check 6 (cache OK for 900+) | Accumulator→shared/indicators.py, verify_harness.py, get_r1000_symbols(), experiment_results stat tests | All 5 baselines match |
| 2 | 174_regime_robustness | DONE | Check 1 (sample OOS symbols for OOS experiments) | get_bull_prob() on MacroRegime, n_obs on Accumulator, get_oos_symbols() | All 6 baselines match |
| ... | | | | | |

## Current Baselines (must match after any infrastructure change)

| Experiment | Train Sharpe | Test Sharpe |
|-----------|-------------|-------------|
| 07_sqqq_spike | +0.495 | +1.177 |
| 08_gold_overnight | +1.373 | +0.692 |
| 09_gold_intraday | +1.037 | +1.358 |
| 10_base_overnight | +1.625 | +3.017 |
| 11_flow_gapfill | +1.059 | +0.449 |
| 12_dynamic_universe | +1.709 | +1.454 |
| 13_regime_robustness | -0.125 | +1.390 |
