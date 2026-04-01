# Experiment Verification & Reporting

STEP 3 (verification) and STEP 4 (reporting). All 8 checks must pass.

---

## STEP 3: VALIDATE (8 checks)

### Check 1: Cache vs Raw Spot-Check
Compare cached data against raw DB for 5 dates × N symbols. Use 3 symbols for small universes, 10+ for large universes (900+). Sample across sectors.

### Check 2: Timezone DST Check
Compute DST transitions dynamically. Log UTC bounds before and after each.

### Check 3: Temporal Trace
Pick one date. Log every data access with timestamps. Causal flag MUST be a boolean expression comparing **distinct** parsed timestamps — `avail <= avail` is tautological and BANNED. Each trace item must compare timestamps from different phases (e.g., `data_available_at < signal_computed_at`).

### Check 4: One-Day Manual Calc
Pick one date. Compute signal and return by hand from raw DB prices. Compare to strategy output (tolerance 1e-8). For cross-sectional strategies, replay the strategy logic to identify which symbol was traded, then verify that symbol's entry/exit prices from raw DB. For multi-leg composites, verify at least one leg's entry/exit prices from raw DB — do NOT just check `equity[T]/equity[T-1] == day_ret[T]` (that is self-referential, A13).

### Check 5: Train/Test Consistency
Report Sharpe for train, test, full. Analyze degradation.

### Check 6: Incremental vs Batch (Gold Standard)

1. **Independent implementation.** Do NOT import from `run_strategy.py`.
2. **Raw data from DB preferred.** For universes ≤200 symbols, use raw DB queries. For large universes (900+), the price cache is acceptable IF Check 1 verifies cache == raw DB. Reason: HMM regime models are nondeterministic across long runs, causing false divergence unrelated to price correctness.
3. **Tolerance 1e-8.** Any threshold above 1e-8 is wrong.
4. **Cumulative equity verification** at sample dates, not just per-day returns.
5. **Same trading day universe.**

### Check 7: Signal Direction

Compare signal-on mean vs **buy-and-hold SPY** (close-to-close daily return). This is the industry standard baseline — does the signal beat the market? Query SPY close-to-close from raw DB. Do NOT use all-days mean of the strategy as benchmark — for event-driven strategies with mostly-zero days, this is trivially near zero and always beatable.

Both train and test independently:
- Cross-period flip (train positive, test negative or vice versa) → FAIL
- Spread < 0 in either period → FAIL

**Enforce programmatically** — do NOT hardcode PASS. A FAIL documents a null result (does not block commit).

### Check 8: Input Data Integrity
Schema validation, sample correctness (5+ dates vs raw source), completeness check for all derived inputs.

---

## Statistical Robustness (MANDATORY after Checks 1-8)

Run 3 tests from `shared/stat_tests.py`:
1. **permutation_test** — pass `tc_per_active_day` so permuted returns pay same TC
2. **bootstrap_sharpe_ci** — is Sharpe distinguishable from zero?
3. **concentration_ratio** — check `signed_top_n_fraction` (>90% = lottery ticket)

---

## STEP 4: REPORT

Write `TEMPORAL_PROOF.md` LAST after all code is finalized. Include wall-clock diagram, temporal audit table, C-class checklist, results. Use function names (not line numbers) for stability.

**GATE:** All 8 checks pass. Temporal audit all Causal=Y. See `rules/prompt-compliance.md` for the commit gate matrix.
