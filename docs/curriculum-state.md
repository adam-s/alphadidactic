# Curriculum State

Read this file at the start of every new orchestrator chat to pick up where we left off.

---

## Current Phase: ITERATION (195 → 00, decomposed)

Running the reference experiment iteration plan from 195 backward. Complex experiments are decomposed into single-leg components. Each leg produces a paradigm example for the composite.

### Next action: Diagnose 06 universe gap (53 vs 153), then decide: fix universe or move to next leg

06_base_overnight rerun complete. MacroRegime working (mostly "bull"). Null result at baseline (Train -0.12, Test 0.87, p=0.30). Two verification patches applied (Check 7 programmatic FAIL, Check 6 all-DB). Key diagnostic: universe is 53 symbols (from cached parquet) vs reference 153. This likely explains the performance gap — smaller universe = fewer candidates = weaker cross-sectional selection. Options: (1) rebuild universe cache with DB fallback `get_symbol_universe()`, rerun; (2) accept the difference and move to next leg.

### Score trajectory (Curriculum)

| Cycle | Reviewer | Adversary | No code bugs? |
|-------|----------|-----------|---------------|
| C01 04_residual_momentum | 23/28 | INVESTIGATE | Yes (grace_minutes, Check 7 benchmark) |
| C02 07_earnings_drift | 23/28 | INVESTIGATE | Yes (duplicate events, C-exit filter, Check 6 cached) |
| C03 08_extrinsic_flow | 20/28 | REJECT | Yes (degenerate signal, C-exit Case 1/2 confusion) |
| C04 13_variance_risk_premium | 28/28 | INVESTIGATE* | Yes (A13 clone, z-score on levels methodology) |
| C05 21_klms_kernel | 28/28 | INVESTIGATE | Yes (VXX history contamination, feature dominance) |
| C06 25_multi_signal_ensemble | 25/28 | PASS | Yes (VXX split proxy, TC flat, A13 borderline) |
| C07 28_three_leg_portfolio | 27/28 | REJECT | Yes (TC every active day, Check 6 shares bug) |
| C07 iter8 | 27/28 | INVESTIGATE | Yes (Check 7 Leg 2 cross-instrument, MaxDD formula) |
| C07 iter9 | 26/28 | INVESTIGATE | Yes (Check 7 Leg 3 BH benchmark diluted by off-day zeros) |
| C07 iter10 | 26/28 | REJECT | Yes (TQQQ 2:1 forward split missed by magnitude filter, R1 gate ordering) |

### Score trajectory (H-series, blind agents)

| Cycle | Reviewer | Adversary | No code bugs? |
|-------|----------|-----------|---------------|
| H1 iter3 | 26/28 | PASS | Yes |
| H2 iter1 | 24/28 | INVESTIGATE | Yes (doc/verification gaps only) |
| H3 iter1 | 27/28 | PASS | Yes |
| H4 iter1 | 24/28 | INVESTIGATE | Yes (metric/doc gaps only) |

Convergence target: 24/28. Achieved at H2, sustained. No code bugs since H1 iter2.

### Score trajectory (Iteration 195→00)

| Cycle | Reviewer | Adversary | No code bugs? | Patches |
|-------|----------|-----------|---------------|---------|
| 195_capital_fix (aborted) | — | — | N/A (too complex, decomposed) | get_symbols() DB fallback, worktree cache copy |
| 03_overnight_momentum iter1 | 27/28 | INVESTIGATE | Yes (Check 3 simplified return, stat test TC bias) | stat_tests.py TC-fair permutation, Check 3 split-aware, TC-fairness rule |
| 03_overnight_momentum iter2 | 27/28 | PASS | Yes (C-exit 0% instead of max loss, never triggers for SPY) | Promoted to paradigm_examples/single-asset-overnight/ |
| 04_gold_overnight_ema | 27/28 | PASS | No code bugs (2 low false alarms) | None needed — prior patches carried forward cleanly |
| 05_vxx_sqqq_hedge | 24/28 | INVESTIGATE | C-exit latent (0%), Check 3 hardcoded flags | stat_tests signed concentration, pending-row C-exit for dataset-builder, Check 3 ban literal True |
| 06_base_overnight (run 1) | — | — | FRED panel broken (1 col not 4) | load_fred_panel() DB builder, Check 8 data integrity, adversary trust boundary, inline audit checklist |
| **DATA INTEGRITY RESET** | — | — | Experiments 03-05 deleted | FRED cache silently broken since project start. MacroRegime returned "unknown" every day. No agent caught it. |
| 06_base_overnight (rerun) | 24/28 | INVESTIGATE | Yes (Check 7 hardcoded PASS, Check 6 uses cached data) | Check 7 programmatic FAIL pattern, Check 6 ALL prices from DB |

**Self-reflection grid (06_base_overnight rerun):**

| Area | Finding |
|------|---------|
| Agent compliance | 58/200 tool calls. Found & fixed accumulator temporal bug (A3-variant) independently. All 8 checks reported PASS but Check 7 should have been FAIL (null result). |
| Missing instructions | **Check 7:** Rules said "spread < 0 → FAIL" but no code pattern — agents hardcode PASS and document in prose. Added explicit programmatic pattern. |
| Vague/contradicted | **Check 6 "raw data, not cached":** Rule said "sample date prices from CursorEngine" but agents read returns from cached parquet for the equity replay. Tightened: ALL prices/returns from DB, never parquet. |
| Monitoring | APC worked throughout. No silent gaps. |
| Orchestrator gaps | Reviewer (24/28) and adversary (INVESTIGATE) converged on same 2 findings — good alignment. |
| Shared infrastructure | `build_fred_panel()` DISTINCT ON pattern flagged as suspicious but correct (ORDER BY is deterministic). Low priority. |
| Verification gaps | Check 7 and Check 6 — both recurring failures across multiple experiments. Patches applied with "RECURRING FAILURE" tag. |
| Token efficiency | 58/200 = 29%. Reasonable for cross-sectional (51 symbols). |
| Generalization | Both patches are general principles. No experiment-specific content. |
| Results | Null result at baseline (Train -0.12, Test 0.87, p=0.30). Optuna Pareto: Train 1.60, Test 0.94, gap 0.66. Universe 53 vs reference 153 — structural difference from cached universe file. |

**Self-reflection grid (06_base_overnight / data integrity discovery):**

| Area | Finding |
|------|---------|
| Agent compliance | Agent ran full pipeline correctly (75 tool calls, Train 1.99). Signal logic matched reference exactly (same params from BEST_CONFIG). |
| Missing instructions | **Check 8 (data integrity) did not exist.** No verification step checked that cached data had the expected schema. MacroRegime silently degraded. |
| Infrastructure gap | `load_fred_panel()` blindly read parquet with no validation. `build_fred_panel()` didn't exist. Cache was a 1-column placeholder from initial setup, never rebuilt. |
| Adversary gap | Adversary had cache-checking language but as optional, not mandatory. Elevated to PRIORITY 3. |
| Monitoring gap | Orchestrator used `sleep 120 && agent_protocol tail` (blocks forever). Fixed: use `monitor_all` (one-shot), poll between productive tasks. |
| Token waste | orchestrator-workflow.md (23KB) was in reference/ but only the orchestrator reads it. Moved to docs/. temporal-details.md was duplicate of bug-catalog.md — deleted. |
| Audit process | Audit checklist was a cross-reference to a file — easy to skip. Inlined the 8 steps directly in CLAUDE.md. |

**Self-reflection grid (03_overnight_momentum iter2):**

| Area | Finding |
|------|---------|
| Agent compliance | Excellent — 41/200 tool calls, 18 APC messages, TC-fair stat test, split-aware Check 3. C-exit structural gap (0% instead of -100% on missing exit). |
| Missing instructions | None new — C-exit rule already exists, agent just didn't implement it. |
| Vague/contradicted | None found. |
| Monitoring | APC excellent (18 messages, started from STEP 0). |
| Orchestrator gaps | None — adversary found only 1 low (half-day extended hours, false alarm). |
| Paradigm example | Promoted to single-asset-overnight. First paradigm example populated. |
| Shared infrastructure | TC-fair permutation verified correct (p=0.041 matches adversary's independent computation). |
| Verification gaps | Half-day close resolution picks extended-hours bar (low severity, CursorEngine design issue). |
| Token efficiency | 41/200 = 21%. Most efficient run yet. |
| Generalization | No new instruction patches needed — iter1 patches proven effective. |

**Self-reflection grid (03_overnight_momentum iter1):**

| Area | Finding |
|------|---------|
| Agent compliance | Good — 52/200 tool calls, 10 APC messages, full pipeline with Optuna. Check 3 used simplified return function. |
| Missing instructions | Statistical tests must compare TC-matched returns. Check 3 must use same return function as strategy. |
| Vague/contradicted | None found. |
| Monitoring | APC worked (10 messages vs 0 on 195). Explicit APC reminder in prompt helped. |
| Orchestrator gaps | Adversary found TC bias I missed in pre-screen. |
| Paradigm example | None exist yet. This experiment is candidate for single-asset-overnight after TC fix. |
| Shared infrastructure | stat_tests.py permutation_test had TC bias (p=0.04 → 0.33). Fixed with tc_per_active_day param. |
| Verification gaps | Check 3 trace masks split bugs with simplified formula. Stat tests mask signal with TC bias. |
| Token efficiency | 52/200 = 26%. Very efficient for single-asset experiment. |
| Generalization | All 3 patches are general principles. |

---

## Prior Phase: BREADTH TESTING (Cycles 1-18)

18 cycles building and testing the instruction set against reference experiments. See cycle log below for full history. Key milestones:
- Cycle 1: First perfect score (28/28)
- Cycle 10: First adversary PASS
- Cycle 18: Instruction set stabilized, moved to hypothesis gap testing

## Cycle Log

### Cycle 1 — 2026-03-23

**Hypothesis:** Overnight momentum in SPY (positive overnight return predicts next positive overnight return)
**Mapped to:** reference_experiments/03_overnight_momentum/
**Agent score:** 28/28 (A: 14/14, C: 8/8, V: 6/6)
**Failures:** None — perfect score on first run
**Key observations:**
- Agent chose pure overnight return; reference used close-to-close. Both valid but ambiguous in hypothesis.
- Agent's verify_integrity.py (454 lines, 6 checks) far more thorough than reference (94 lines, 1 check)
- Agent correctly implemented C-exit (max loss on missing exit); reference used 0.0 return
- Pending-row pattern implemented correctly in all three locations (strategy + both verification loops)
**Instruction patches:**
- experiment-protocol.md: Added return target specification to STEP 0 PRE-FLIGHT
- pending-row-pattern.md: Acknowledged dataset-builder approach as equivalent valid implementation
**Infrastructure patches:** None needed
**Commit:** f16d274, 942cc73

<!-- Template for cycle entries:

### Cycle N — [date]

**Hypothesis:** [description]
**Mapped to:** reference_experiments/XX_name/
**Agent score:** /28 (A: /14, C: /8, V: /6)
**Failures:**
- [description of what went wrong]
**Instruction patches:**
- [rule file]: [what changed and why]
**Infrastructure patches:**
- [shared/ file]: [what changed — new safety check, better defaults, etc.]
**Commit:** [hash]

-->

## What Gets Updated Each Cycle

Every cycle can produce two kinds of improvements:

1. **Instruction patches** (`.claude/rules/`, `.claude/reference/`, `.claude/agents/`): When the agent makes a mistake because a rule is missing, unclear, or insufficient. These teach the next agent what to do differently.

2. **Infrastructure patches** (`shared/`): When the agent makes a mistake because the tooling doesn't prevent it. Example: the agent writes a query that exceeds the density tier — we might add a runtime check in `db_monitor.py` that raises before the query executes, or add a helper function to `temporal_sources.py` that makes the safe path easier than the unsafe path.

Both are first-class outputs of the loop. The instruction set and the infrastructure co-evolve.

### Cycle 2 — 2026-03-23

**Hypothesis:** Yield curve regime (T10Y2Y z-score) predicts SPY overnight returns
**Mapped to:** reference_experiments/05_yield_curve_regime/
**Agent score:** 26/28 (A: 12/14, C: 8/8, V: 6/6)
**Failures:**
- A3 (-1): FRED publication lag implemented via raw SQL with BDay subtraction instead of FredLatestSource
- A6 (-1): Same root cause — raw SQL for FRED bypasses temporal_sources infrastructure
**Key observations:**
- Agent didn't know FredLatestSource existed — instructions said "use temporal_sources" but didn't call out FRED specifically
- Agent improvised correctly (lag math was right) but method is fragile and not auditable
- numpy.bool_ concern was a false alarm — agent correctly used bool() cast
- Design divergences (return target, lookback window) are legitimate research choices
**Instruction patches:**
- temporal-correctness.md: Added FRED/macro section — must use FredLatestSource, lag modeled forward not backward
- experiment-agent.md: Added H7 hard stop for raw FRED SQL
**Infrastructure patches:** None needed
**Commit:** f16d274, 942cc73

### Cycle 3 — 2026-03-24

**Hypothesis:** Options extrinsic premium flow predicts SPY overnight returns
**Mapped to:** reference_experiments/08_extrinsic_flow/
**Agent score:** 25/28 (A: 13/14, C: 7/8, V: 5/6)
**Failures:**
- A6 (-1): Raw SQL for minute_bar prices instead of CursorEngine; custom extrinsic premium computation instead of shared/flow_aggregation.py
- C3 (-1): Missing price days skipped (continue) instead of max loss
- V3 (-1): Wall-clock diagram missing trading calendar query and rolling window data availability
**Key observations:**
- Agent didn't know shared/flow_aggregation.py existed — same pattern as cycle 2 with FRED
- Instructions say "use shared infrastructure" but don't enumerate available modules
- Agent correctly used OptionsTradesSource for options data and respected EXTREME density tier
- numpy.bool_ false alarm appeared again — confirmed non-issue for third time
- Agent designed SPY-only strategy; reference uses cross-sectional 50-stock ranking (design divergence, not a bug)
**Instruction patches:**
- experiment-protocol.md: STEP 1 now enumerates shared modules (flow_aggregation, live_loop calendar) and requires reading shared/ directory listing before writing data-fetching code
- experiment-agent.md: Added item 9 to reading list — ls shared/ to discover available modules
**Infrastructure patches:** None needed
**Commit:** f16d274, 942cc73

### Cycle 3c — 2026-03-24

**Hypothesis:** Options extrinsic premium flow predicts SPY overnight returns (re-run with APC progress reporting)
**Mapped to:** reference_experiments/08_extrinsic_flow/
**Agent score:** TBD (reviewer not run yet — focus was on feedback loop)
**Total runtime:** 33 minutes (STEP 1: 24 min I/O bound, STEP 2-4: 9 min)
**Feedback loop results:**
- APC agent protocol: STEP 0 visible in dashboard within 1 minute ✓
- PROGRESS.md: Updated for all 5 steps with timing and metrics ✓
- Inner-loop progress during STEP 1 (24 min): NOT visible — ScriptProgress not yet used by agent ✗
- DB monitoring: Active queries visible via db_monitor status ✓
- File appearance tracking: Dataset/results files visible via ls ✓
**Key finding:** Step-level progress works. Inner-loop progress (day N/1029) is the remaining gap. The agent wrote PROGRESS.md after each step but didn't use ScriptProgress inside build_dataset.py's day loop.
**Instruction patches needed:**
- Enforce ScriptProgress.tick() in all loops >100 iterations
- Make progress reporting a verifiable requirement (reviewer checks for it)
**Commit:** f16d274, 942cc73

### Cycle 4 — 2026-03-24

**Hypothesis:** EOD reversal — last-hour losers (15:00→15:52 ET) revert the next day
**Mapped to:** reference_experiments/09_eod_reversal/
**Agent score:** 26/28 (A: 13/14, C: 8/8, V: 5/6)
**Adversary:** INVESTIGATE (5 findings: 1 critical, 2 high, 2 medium)
**Failures:**
- A7 (-1): Magnitude-based filter (SPLIT_THRESHOLD=0.20) not calibrated to leveraged ETF universe — 100% false positive rate on 12 events
- V1 (-1): Check 3 temporal trace hardcoded Saturday (2024-06-15) — vacuously true
**Adversary findings (beyond reviewer):**
- CRITICAL: All 12 neutralized returns were legitimate (TSLL has zero splits in ledger)
- HIGH: Check 6 (incremental/batch) clones strategy code including the filter — A13 self-referential audit, cannot detect the bug
- MEDIUM: Check 7 always returns True — cannot gate experiments
- SUSPICIOUS: Strategy is a market-direction bet (r=-0.59 with SPY), not cross-sectional reversal
**Instruction patches:**
- accounting-correctness.md: C-split — magnitude-based filters must match instrument range (generalized, not ETF-specific)
- experiment-protocol.md: Check 3 must use dynamically selected trading day; Check 6 must be independently implemented (A13 prevention); Check 7 must have real failure criteria
- workflow.md: APC monitoring protocol (6 rules), background agents mandatory, escalating poll schedule
- SKILL.md: Commit after every cycle, not just final
- CLAUDE.md: Instruction maintenance rule 1 — always generalize, never write specific rules
**Infrastructure patches:**
- system_monitor.py: Missing pathlib import in write_to_apc()
**Commit:** 0ba7eeb, 6cc4069, b43d827, a24d585

### Cycle 5 — 2026-03-25

**Hypothesis:** Residual momentum (cumulative market-neutral returns over 60 days) predicts cross-sectional stock returns in 50 S&P 500 stocks
**Mapped to:** reference_experiments/04_residual_momentum/
**Agent score:** 24/28 (A: 13/14, C: 6/8, V: 5/6)
**Adversary:** INVESTIGATE (6 findings: 1 critical, 1 medium, 4 low)
**Failures:**

- A6 (-1): Custom SQL in build_dataset.py instead of cursor_engine/temporal_sources
- C3 (-2): Missing exit price returns 0% instead of max loss
- V2 (-1): Check 6 incremental path shares split function with batch path (partial A13 violation)
**Adversary findings (beyond reviewer):**
- CRITICAL: Signal is identically zero by OLS normal equations — sum of regression residuals with intercept = 0 by construction. Portfolio selection is floating-point noise. Experiment never tested residual momentum.
- MEDIUM: TC model ignores actual turnover (flat 2*TC on rebalance days assumes 100% turnover)
- LOW: Check 3 temporal trace hardcodes causal=True (static assertion, can't fail)
- LOW: Sharpe diluted by flat warm-up period days
**Instruction patches:**
- experiment-protocol.md: Signal degeneracy check in STEP 2 (cross-sectional std must be > 1e-10); Check 6 independence clarified (signal chain must be reimplemented, data correctness infra may be reused with justification); Check 3 causal flag must be computed dynamically; Pre-flight item 8 (date range from config, document discrepancies)
- accounting-correctness.md: C-exit rewritten as two-case rule (minute-bar gap → same-day resolution earlier→later; no same-day price → max loss); C-TC must be proportional to realized turnover for non-daily strategies
**Infrastructure patches:**
- cursor_engine.py: New `BEFORE_THEN_AFTER` resolution mode (look earlier first, fall back to later, same-day only)
**Commit:** 9df1cb3

### Cycle 6 — 2026-03-25

**Hypothesis:** Post-earnings announcement drift (PEAD) — stocks with positive earnings surprises drift up
**Mapped to:** reference_experiments/07_earnings_drift/
**Agent score:** 24/28 (A: 13/14, C: 6/8, V: 5/6)
**Adversary:** INVESTIGATE (6 findings: 0 critical, 3 medium, 3 low)
**Failures:**

- A3 (-1): AMC events on non-trading days between consecutive trading days missed (holiday gap)
- C3 (-2): Rows with missing p_close_T1 dropped via dropna — survivorship bias
- V2 (-1): Check 6 doesn't verify temporal routing (BMO/AMC) embedded in dataset
**Adversary findings (beyond reviewer):**
- MEDIUM: `available_at_utc` from EarningsReleasesSource never checked — relies on `reporting_time` string label
- MEDIUM: Check 7 uses full-sample spread, not per-period — could mask test-period reversal
- LOW-MEDIUM: No price floor filter — penny stocks distort equal-weight returns
**Key observations:**
- Agent correctly used EarningsReleasesSource from shared infrastructure (A6 fix from cycle 5 working)
- Publication lag modeled structurally (BMO same-day, AMC next-day)
- Signal non-degenerate (std=17,996) — degeneracy check from cycle 5 would pass
- Null result: Train Sharpe 0.128, Test -0.533 (signal direction correct but edge too thin)
- C-exit keeps failing: cycle 5 = 0% return, cycle 6 = dropna. Same root cause, different manifestation
**Instruction patches:**
- accounting-correctness.md: C-exit now explicitly forbids dropna on exit prices in dataset builders
- experiment-protocol.md: Check 6 for dataset-builders must verify temporal routing; Check 7 must check direction per-period independently
- temporal-correctness.md: Calendar gaps between trading days; use `available_at` timestamps, not metadata labels
**Infrastructure patches:** None
**Commit:** f6e84b0

### Cycle 7 — 2026-03-25

**Hypothesis:** Residual momentum v2 (corrected Blitz-Huij-Martens: beta on 120-day window, applied residuals on 60-day momentum window)
**Mapped to:** reference_experiments/04_residual_momentum/
**Agent score:** 26/28 (A: 13/14, C: 8/8, V: 5/6) — best score yet
**Adversary:** INVESTIGATE (0 bugs, 5 methodological concerns)
**Failures:**

- A6 (-1): Custom SQL for batch cross-sectional queries (shared infra doesn't support batch pattern)
- V2 (-1): Check 6 silently relaxes tolerance from 1e-8 to 1e-6
**What's FIXED from prior cycles:**
- C-exit PASS (was FAIL in cycles 3, 5, 6) — missing prices now -1.0 max loss
- TC PASS — proportional to realized turnover
- Signal non-degenerate — correct Blitz-Huij-Martens approach (applied residuals, not OLS residuals)
- Check 7 PASS — direction checked per-period
**Adversary findings:**
- HIGH: Sub-period Aug 2025–Feb 2026 Sharpe = 3.055 (exceeds 3.0 hard stop)
- HIGH: Only 14 monthly observations in test, p=0.057 (not significant at 5%)
- HIGH: SNDK outlier (+1225%) drives 0.18 of test Sharpe
- MODERATE: Survivorship bias — current S&P 500, not point-in-time
- LOW: Parameters differ from reference (not pre-registered)
**Results:** Train Sharpe 0.974, Test Sharpe 1.839, Full 1.259, Total Return 170.7%
**Instruction patches:**
- experiment-protocol.md: Sub-period Sharpe check (≥6 months); Check 6 tolerance must log WARNING; pre-flight warning exceedance requires structured investigation
**Commit:** 7174eb0

### Cycle 8 — 2026-03-25

**Hypothesis:** Multi-asset overnight momentum on XLK, XLE, XLF (position overlap test)
**Mapped to:** No direct reference (novel hypothesis)
**Agent score:** 26/28 (A: 14/14, C: 8/8, V: 4/6) — first perfect A+C score
**Adversary:** INVESTIGATE (0 bugs, 1 reporting inconsistency, 5 minor findings)
**Failures:**

- V1 (-1): Half-day detection mismatch between strategy (ALL symbols null) and verifier (ANY symbol null)
- V2 (-1): Check 6 only tests XLK, not XLE or XLF
**Key achievements:**
- A6 PASS — CursorEngine used (first non-trivial experiment to pass A6)
- C2/C8 PASS — position overlap correctly handled with 1/N weighting on first attempt
- C-exit PASS — third consecutive cycle (fix is durable)
- A-class + C-class = 22/22 (perfect correctness, deductions only in verification)
**Adversary findings:**
- INVESTIGATE: Overlap stats (402+308+309=1019) don't match In Market=84.7% (~863 days) — reporting inconsistency
- MINOR: Check 6 validates only XLK signal, not full portfolio
- MINOR: SPY benchmark uses close-to-close vs strategy's overnight returns
- MINOR: Check 7 has triple-assignment dead code
**Results:** Train Sharpe -0.016, Test 0.703, Full 0.172 (null result, Check 7 FAIL)
**Instruction patches:**
- experiment-protocol.md: Check 6 multi-asset coverage + trading day universe consistency
- pending-row-pattern.md: Benchmark must use same return type as strategy
**Commit:** 78f4df9

### Cycle 9 — 2026-03-25

**Hypothesis:** Cross-asset: SPY negative overnight return → go long GLD overnight (flight-to-safety)
**Mapped to:** No direct reference (novel hypothesis)
**Agent score:** 26/28 (A: 14/14, C: 8/8, V: 4/6)
**Adversary:** INVESTIGATE (0 bugs, devastating economic critique)
**Failures:**

- V1 (-1): Check 7 should FAIL — signal inverted in training, test positive only from gold bull market
- V2 (-1): Check 6 only verifies SPY signal, not GLD return/equity chain
**Key findings:**
- A+C perfect (22/22) — 3rd consecutive cycle
- GLD unconditional overnight Sharpe in test = 2.259, strategy = 2.222 — signal UNDERPERFORMS always-long
- 15.9% of random signals beat the strategy (not statistically significant)
- Agent correctly identified null result but Check 7 criteria too lenient
- numpy.bool_ comparison (`is True` vs `== True`) caused actual bug (first run Sharpe=0.0) — was "false alarm" in earlier cycles but real bug here
**Instruction patches:**
- Check 7: opposing train/test directions = FAIL; unconditional benchmark comparison mandatory
- Check 6: cross-asset must verify both signal AND return instruments
**Commit:** 06d1b68

### Cycle 10 — 2026-03-25

**Hypothesis:** Credit spread regime (FRED BAMLH0A0HYM2 above 60-day MA → long SPY overnight)
**Mapped to:** reference_experiments/05_yield_curve_regime/ (similar FRED pattern)
**Agent score:** 26/28 (A: 13/14, C: 8/8, V: 5/6)
**Adversary:** **PASS** — first clean pass in all 10 cycles
**Failures:**

- A6 (-1): Raw SQL as panel loader for FredLatestSource (gray area — temporal chain preserved)
- V2 (-1): Check 6 reads position from strategy output instead of recomputing independently
**Key achievements:**
- A3 PASS — FRED publication lag correctly handled via FredLatestSource (was FAIL in cycle 2)
- C-class 8/8 — 4th consecutive perfect score
- V improved to 5/6 (was 4/6 in cycles 8-9)
- First adversary PASS — "well-executed null result, unable to break it"
- All cycle 2 failures (A3, A6) now fixed for FRED/macro experiments
**Results:** Train Sharpe -0.234, Test -0.569 (null, signal consistently wrong)
**Instruction patches:**
- experiment-protocol.md: Check 6 full chain verification (don't read intermediate state)
- temporal-correctness.md: Clarify raw SQL as panel loader for shared sources is acceptable
**Commit:** 6a72a03

### Cycle 11 — 2026-03-26

**Hypothesis:** Intraday momentum — morning return (open to 10:30) predicts afternoon drift (10:30 to 15:52)
**Mapped to:** reference_experiments/18_intraday_momentum/
**Agent score:** 26/28 (A: 14/14, C: 7/8, V: 5/6)
**Adversary:** **PASS** — second clean pass. No bugs. Signal is pure market beta selection (zero residual alpha).
**Failures:**

- C3 (-1): Missing p1552 with valid signal prices → 0% instead of max loss
- V2 (-1): Check 6 verifies per-row returns but not cumulative equity chain
**Key achievements:**
- A-class 14/14 — first intraday experiment, temporal argument correct
- Agent correctly identified that pending-row lag is unnecessary for intraday (signal at 10:30 → return 10:30-15:52)
- 64-line deliberation in run_strategy.py about same-day pairing — correct conclusion, unclear instructions
**Adversary findings (beyond reviewer):**
- Signal has zero residual alpha after beta removal (SPY residual Sharpe 0.010)
- Test period not significant (p=0.38 SPY, p=0.73 QQQ)
- BH benchmark TC asymmetry inflates apparent advantage by ~30%
**Results:** SPY Train 0.308, Test 0.216. QQQ Train 0.655, Test -0.156.
**Instruction patches:**
- pending-row-pattern.md: Point-in-time principle + intraday exception (no lag needed when time flows forward)
- experiment-protocol.md: Check 6 cumulative equity verification, Check 6 return prices from raw DB, Check 7 benchmark TC methodology
- accounting-correctness.md: C-exit Case 3 (missing entry price → resolve first, then non-trade)
**Commit:** f16d274, 942cc73

### Cycle 12 — 2026-03-26

**Hypothesis:** Turn-of-month calendar anomaly — SPY held during last 3 + first 3 trading days of each month
**Mapped to:** reference_experiments/19_turn_of_month/
**Agent score:** 23/28 (A: 11/14, C: 7/8, V: 5/6)
**Adversary:** **INVESTIGATE** — 3 confirmed bugs
**Failures:**

- A5 (-1): Multi-day trading calendar query on hypertable
- A6 (-2): Raw SQL instead of CursorEngine (persistent gap)
- C3 (-1): NaN close_prev → 0% instead of resolution attempt
- V2 (-1): Check 6 opens DB connection but never queries — reads cached parquet
**Adversary findings:**
- CONFIRMED: TC overcharge ~5.9x — flat 2*TC per held day instead of per trade (train Sharpe 0.17→0.44 with fix)
- CONFIRMED: Check 6 A13 violation — return path not independent (opens conn, never uses it)
- CONFIRMED: TOM truncation bias — first partial month misclassifies 3 days
**Results:** Train 0.165, Test -1.352 (null, signal reversed in test)
**Instruction patches:**
- experiment-protocol.md: Partial-period calendar signal warning (STEP 0 item 9)
**Commit:** f16d274, 942cc73

### Cycle 13 — 2026-03-26

**Hypothesis:** Yield curve regime → sector basket rotation (cyclical vs defensive)
**Mapped to:** reference_experiments/15_sector_rotation/
**Agent score:** 23/28 (A: 11/14, C: 7/8, V: 5/6)
**Adversary:** **INVESTIGATE** — 1 critical, 1 high, 3 medium
**Failures:**

- A3 (-1): FRED lag modeled manually with BDay(1), not FredLatestSource
- A5 (-1): Trading calendar scans full hypertable range
- A6 (-1): Raw SQL instead of CursorEngine for prices
- C3 (-1): Missing exit price = 0% instead of max loss
- V2 (-1): Check 6 reads parquet, not raw DB
**Adversary findings:**
- CRITICAL: Check 7 benchmark doesn't apply split protection — phantom -50% loss from 3-ETF split day makes strategy look like it adds alpha. With corrected benchmarks, strategy underperforms every unconditional benchmark.
- HIGH: Z-score self-inclusion — current value appended to window before computing z-score (max delta 0.82 z-score units vs reference)
- MEDIUM: Hardcoded causal flag in Check 3
- MEDIUM: Check 6 equity chain not independent
- MEDIUM: Half-day close prices handled as 0%
**Results:** Train 0.485, Test 0.961 (positive but no alpha over beta)
**Instruction patches:**
- experiment-protocol.md: Rolling window self-inclusion check; event-strategy holding period check; position sizing hard stop
- accounting-correctness.md: C-split event strategy calibration; C-sizing (position sizing from point-in-time info)
**Commit:** 1c48f4d, b506cb7

### Cycle 14 — 2026-03-26

**Hypothesis:** Pre-earnings institutional call accumulation → post-earnings returns (multi-day hold)
**Mapped to:** reference_experiments/16_pre_earnings_flow/
**Agent score:** 20/28 (A: 9/14, C: 7/8, V: 4/6) — lowest score
**Adversary:** **REJECT** — first rejection. 1 critical, 2 high, 2 medium
**Failures:**

- A1 (-1): Position sizing uses forward-looking max_global_concurrent
- A3 (-1): Earnings dates pre-loaded without EarningsReleasesSource
- A5 (-1): Multi-year hypertable scan for trading calendar
- A6 (-2): No shared infrastructure (raw SQL for options, earnings, prices)
- C1 (-1): Position fraction from forward-looking data
- V1 (-1): Hardcoded causal flag in Check 3
- V2 (-1): Check 6 only verifies signal, not return/equity chain
**Adversary findings:**
- CRITICAL: Split filter false positives — ALL 78 flagged rows are legitimate earnings moves (zero actual splits). Train Sharpe 0.239→0.894 with fix. SPLIT_THRESHOLD=0.20 inappropriate for earnings strategy.
- HIGH: Forward-looking position sizing flips Sharpe sign (0.12→-0.24 with dynamic sizing)
- HIGH: 59% of trades exit before earnings — strategy doesn't test its hypothesis (HOLD_DAYS=2 too short)
- MEDIUM: 4.9x pseudo-replication from overlapping signal windows
- MEDIUM: Simultaneous execution (signal at 16:00 ET, entry at 16:00 ET)
**Results:** Train 0.239, Test -0.161 (null, but contaminated by interacting bugs)
**Instruction patches:**
- Same as cycle 13 (patches applied together)
**Commit:** 1c48f4d, b506cb7

### Cycle 15 — 2026-03-26

**Hypothesis:** Put/call premium imbalance in late-session options (15:55-16:00 ET) → SPY overnight
**Mapped to:** reference_experiments/02_put_call/
**Agent score:** 23/28 (A: 13/14, C: 6/8, V: 4/6)
**Adversary:** **REJECT** — double-lag return pairing (dataset embeds lag + strategy adds prev_decision)
**Failures:**

- A4 (-1): Vectorized SMA precomputed outside loop
- C3 (-2): Missing price = 0% instead of max loss
- V1 (-1): Hardcoded causal flag + incomplete Check 6
- V2 (-1): Check 6 verifies dataset only, not equity chain
**Adversary findings:**
- CRITICAL: Double-lag — dataset pairs signal[T]→return[T→T+1], then prev_decision shifts again. Every trade earns wrong night. Train Sharpe 0.234→-1.135 with fix.
- MEDIUM: C-exit 0% on 3 half-days
- MEDIUM: SMA NaN propagation creates 60-day signal blackout
**Results:** Train 0.234, Test -0.755 (null, but numbers are wrong due to double-lag)
**A6: 2/2** — SimpleFlowSource + CursorEngine. Guide worked.
**Instruction patches:**
- pending-row-pattern.md: Anti-Pattern 0 (double-lag warning)
**Commit:** e0cf7fa, 900211f

### Cycle 16 — 2026-03-26

**Hypothesis:** VXX z-score + realized vol → SPY long/flat
**Mapped to:** reference_experiments/13_variance_risk_premium/
**Agent score:** 26/28 (A: 14/14, C: 7/8, V: 5/6)
**Adversary:** **PASS** (conditional) — no false positives, null result genuine
**Failures:**

- C3 (-1): Missing SPY close with active decision = 0% instead of max loss
- V3 (-1): Wall-clock diagram lacks buffer update timing
**Adversary findings:**
- MEDIUM: C-exit on half-day sessions (3 dates)
- MEDIUM: TC applied per held day, not per trade (8.5x overstatement)
- LOW: Hardcoded causal flags (3/4 entries)
**Results:** All modes near-zero or negative test Sharpe. Null result.
**A6: 2/2** — CursorEngine for both VXX and SPY. Guide worked.
**Instruction patches:**
- Same as cycle 15 (patches applied together)
**Commit:** e0cf7fa, 900211f

### Cycle 17 — 2026-03-26

**Hypothesis:** Safe-haven ratio z-scores (GLD/SPY) predict risk-on/off → long SPY
**Mapped to:** reference_experiments/14_flight_to_safety/
**Agent score:** 27/28 (A: 14/14, C: 8/8, V: 5/6) — highest score
**Adversary:** **PASS** — no confirmed bugs. Validated pending-row by showing same-day Sharpe=2.27 vs correct 0.068.
**Failures:**

- V2 (-1): Check 6 equity chain uses dataset prices instead of raw DB
**Key achievements:**
- A-class 14/14 + C-class 8/8 = 22/22 perfect correctness
- A6 2/2 (CursorEngine for SPY/GLD/TLT, no raw SQL)
- grace_minutes_before=390 for early-close days (adversary found after-hours bar risk but negligible impact)
**Results:** Train -0.075, Test 0.749 (null, Check 7 FAIL — direction inconsistent)
**Commit:** b4b8bdb

### Cycle 18 — 2026-03-26

**Hypothesis:** Cross-sectional put/call premium ratio ranking → next-day returns (307 symbols via flow_cache)
**Mapped to:** reference_experiments/10_option_volume_ratio/
**Agent score:** 22/28 (A: 13/14, C: 7/8, V: 2/6)
**Adversary:** **INVESTIGATE** — TC double-count, look-ahead universe
**Failures:**

- A6 (-1): Check 1 claims flow_cache vs raw DB verification but never implements it
- C4 (-1): TC literal in common.py instead of importing from shared/config.py
- V1 (-1): Check 7 uses raw returns, strategy uses split-adjusted (A13 gap)
- V2 (-2): Check 6 no cumulative equity, cached prices, 1% tolerance
- V3 (-1): Wall-clock diagram doesn't document flow_cache provenance
**Adversary findings:**
- HIGH: TC double-counting (2x overcharge) — Test Sharpe 0.079→0.226 with fix
- MEDIUM: Look-ahead universe (43/307 symbols via full-period median flow filter)
- MEDIUM: Check 1 flow verification claimed but not implemented
- MEDIUM: Check 6 incomplete (cached prices, no equity chain)
**Results:** Train 0.120, Test 0.079 (weak positive, but 0.226 with corrected TC)
**A6: 2/2** — CursorEngine + flow_cache. Guide worked.
**Instruction patches:**
- experiment-protocol.md: point-in-time universe construction, Check 1 cache verification, Check 6 docstring accuracy
**Commit:** c2e5fb4

### C02 — 2026-03-28

**Hypothesis:** Buy positive EPS surprises on day after release when yield curve is risk-on; equal-weight overnight.
**Mapped to:** experiment-curriculum.md #2 (07_earnings_drift)
**Agent score:** 23/28 (A: 13/14, C: 6/8, V: 4/6)
**Adversary:** INVESTIGATE (0 critical, 2 high, 2 medium, 1 low)
**Failures:**

- A7 (-1): Check 6 incremental path doesn't replicate split ledger check
- C3 (-2): Missing close price with valid open filtered as non-trade (0%) instead of max loss
- V2 (-2): Check 6 uses cached parquet, not raw DB prices for computation
**Adversary findings (beyond reviewer):**
- HIGH: 5,343 duplicate (symbol, entry_date) earnings events from DB — 20.2% of positive surprises get 2x weight
- HIGH: Check 6 A13 self-referential (cached parquet, not raw DB)
- MEDIUM: Sparse z-score window — computed on 965 event dates only, missing 57 trading days
- MEDIUM: Risk-on filter value-destructive — risk-on return -0.006% vs risk-off +0.023%
- LOW: Check 4 manual calc skips split ledger (pass in branch)
**Results:** Train Sharpe -0.138, Test -1.728 (null result, signal direction correct but economically insufficient)
**Instruction patches:**
- accounting-correctness.md: Added C-dup (event deduplication), strengthened C-exit for event strategies
- experiment-agent.md: Added web search in STEP 0 (2-3 targeted searches to ground parameter choices)
- research-reviewer-agent.md: Added web search for signal class verification
- adversary-agent.md: Added web search for known pitfalls
**Infrastructure patches:** None
**Commit:** d77c0a2

### C03 — 2026-03-28

**Hypothesis:** Rank stocks by institutional extrinsic call ratio (high = bullish); long top-20 overnight.
**Mapped to:** experiment-curriculum.md #3 (08_extrinsic_flow)
**Agent score:** 20/28 (A: 13/14, C: 7/8, V: 0/6)
**Adversary:** INVESTIGATE (degenerate signal, C-exit misapplication)
**Failures:**

- A7 (-1): SPLIT_THRESHOLD=0.20 not calibrated per instrument in cross-sectional universe
- C1 (-1): Missing-entry skip changes effective position weight (18 at 1/18 vs 20 at 1/20)
- V1 (-2): Check 6 FAIL — max_delta=5.56e-03 on last date (boundary issue)
- V2 (-2): Check 6 fails 1e-8 tolerance
- V3 (-2): No TEMPORAL_PROOF.md — STEP 4 not completed
**Adversary findings:**
- CRITICAL: Degenerate signal — all top-N have call_ext_ratio=1.0, ranking is pandas tie-breaking noise
- CRITICAL: C-exit Case 1/2 confusion — 4 of 5 "max loss" positions had same-day prices available (actual returns -0.6% to +4.7%, not -100%)
- HIGH: Check 6 non-independence (recurring)
**Results:** Agent did not complete TEMPORAL_PROOF.md; Check 6 and Check 7 both FAIL
**Instruction patches:**
- experiment-pipeline skill: Ratio signal degeneracy check (binary clustering at 0.0/1.0), minimum activity threshold
- accounting-details.md: C-exit decision tree (must attempt same-day resolution before max loss)
- orchestrator-workflow.md: Added mandatory instruction audit process
**Infrastructure patches:** None
**Commit:** d84aa97

### C04 — 2026-03-28

**Hypothesis:** Long SPY overnight when VXX z-score is depressed AND vol regime is calm.
**Mapped to:** experiment-curriculum.md #4 (13_variance_risk_premium)
**Agent score:** 28/28 (A: 14/14, C: 8/8, V: 6/6) — first perfect score in curriculum
**Adversary:** INVESTIGATE* (incomplete — ran out of tool budget at Attack 12)
**Failures:** None (perfect reviewer score)
**Adversary findings:**
- HIGH: Check 6 A13 clone — incremental path is variable-rename copy of run_strategy.py
- HIGH: Z-score on cumulative VXX levels biased by structural decay (fires 58.9% vs expected 27.8%)
- MEDIUM: Expanding median regime threshold drifts (train 0.24 → test 0.14)
**Results:** Train Sharpe 0.525, Test -0.319 (null result, direction inverts in test)
**Key observations:**
- Agent fabricated web search citations (0 actual WebSearch calls, plausible-looking references from training)
- Statistical robustness tests not run (advisory, not mandatory)
- VXX split handling correct via authoritative ledger
**Instruction patches:**
- experiment-pipeline skill: Moved web search to STEP 0a with explicit tool naming, added STEP 2b mandatory stat tests
- experiment-checks.md: Statistical robustness changed from advisory to MANDATORY (3 minimum tests)
- prompt-compliance.md: Added checks #10 (WebSearch) and #11 (stat tests) to commit gate
- statistical-analysis.md: Changed from advisory to mandatory
**Infrastructure patches:** None
**Commit:** 3310abe

### C05 — 2026-03-28

**Hypothesis:** KLMS adaptive kernel filter learns mapping from 6 intraday features to overnight SPY returns.
**Mapped to:** experiment-curriculum.md #5 (21_klms_kernel)
**Agent score:** 28/28 (A: 14/14, C: 8/8, V: 6/6) — second consecutive perfect score
**Adversary:** INVESTIGATE (0 critical, 2 medium)
**Failures:** None (perfect reviewer score)
**Adversary findings:**
- MEDIUM: VXX history contamination — mixed pre/post-split prices in rolling z-score (z=69.9 on split day)
- MEDIUM: T10Y2Y feature dominates kernel distance by ~11x, KLMS effectively single-feature model
- SUSPICIOUS: Signal frequency jumps from 48% (train) to 85% (test)
**Results:** Train Sharpe 0.824, Test -0.111 (null). Permutation p=0.604. All 7 checks PASS.
**Key verification: CRITICAL FIX CONFIRMED**
- WebSearch: 3 real calls with actual URLs (ResearchGate, SSRN, ScienceDirect, ACM, Wiley, PMC) ✓
- Stat tests: 3 mandatory tests ran and reported (permutation, bootstrap CI, concentration) ✓
- Check 6 A13: Independent implementation (NOT cloned) ✓
- Root cause: agent was following 5-line summary, never reading 180-line skill body. Fixed by updating agent file to explicitly read skill file.
**Instruction patches:**
- CRITICAL: Fixed broken file reference in experiment-agent.md (pointed to nonexistent file)
- Strengthened audit: added broken-reference check and compliance verification steps
- No new rule patches needed (C05 scored perfectly)
**Infrastructure patches:** None
**Commit:** 28583c6

### C06 — 2026-03-28

**Hypothesis:** Combine 5 signals (yield curve, credit spread, VXX, momentum, turn-of-month); long SPY when >= threshold votes positive.
**Mapped to:** experiment-curriculum.md #6 (25_multi_signal_ensemble)
**Agent score:** 25/28 (A: 13/14, C: 7/8, V: 5/6)
**Adversary:** INVESTIGATE* (output incomplete)
**Deductions:**
- A7 (-1): VXX split uses magnitude proxy only, not authoritative ledger as PREFLIGHT promised
- C4 (-1): TC flat 2*TC per trade day, not documented as proportional for binary strategy
- V2 (-1): Check 6 EMA mirrors run_strategy.py (borderline A13)
**Results:** Train Sharpe -0.035, Test 0.215 (null). Permutation p=0.961. All mandatory steps followed.
**Self-reflection:**
- WebSearch: 3 calls with real URLs — fix continues to hold (3/3 since fix)
- Stat tests: all 3 ran — fix holds
- Orchestrator hung when spawning Explore agents to read large output files — fixed by adding "Reading Agent Output" section to workflow (use bash, not sub-agents)
**Instruction patches:**
- orchestrator-workflow.md: Added "Reading Agent Output" section — use bash for output reading, never sub-agents
**Commit:** 264deef

### C07 — 2026-03-28

**Hypothesis:** 3-leg portfolio: QQQ growth (30%), TQQQ/SQQQ hedge (45%), GLD intrinsic value (25%).
**Mapped to:** experiment-curriculum.md #7 (28_three_leg_portfolio)
**Agent score:** 27/28 (A: 14/14, C: 8/8, V: 5/6)
**Adversary:** REJECT — CRITICAL: TC charged every active day instead of only on turnover (28.82% excess drag, return -1.07% → +34.84% corrected)
**Deductions:**
- V1 (-1): Check 7 hardcodes True instead of computing actual direction result
**Adversary findings:**
- CRITICAL: Flat 2*TC on every active day. Passive always-long leg pays TC 1028 times when it should pay once. Corrected return +34.84% (still below B&H +39.88%).
- HIGH: Check 6 shares identical TC bug — both paths unconditionally apply costs
- MEDIUM: Check 7 hardcoded True
**Results:** Train Sharpe 0.078, Test ~flat. Permutation p=1.000. TC bug makes reported equity curve wrong by ~36%.
**Self-reflection:**
- WebSearch: 3 calls (4/4 since fix). Stat tests: 3 ran (4/4 since fix).
- CRITICAL: I pre-screened and missed the TC bug. Reviewer also missed it (gave C4 2/2). Only the adversary caught it.
- Root cause: C-TC rule said "proportional to turnover" but lacked explicit anti-pattern for the flat-per-day case.
- Check 6 shared the bug because both paths used identical accounting logic (A13 in spirit).
**Improvement list (top 3 implemented):**
1. C-TC anti-pattern: costs only on position changes, not every active day
2. Check 6 independence: must not import from run_strategy.py, must catch accounting bugs
3. Pre-screen checklist: orchestrator scans for TC, C-exit, Check 6 imports, Check 7 hardcode before launching reviewer/adversary
**Instruction patches:**
- accounting-correctness.md: C-TC anti-pattern (flat daily cost = wrong for hold-through strategies)
- experiment-checks.md: Check 6 must not import from run_strategy.py, must independently verify accounting
- orchestrator-workflow.md: Added pre-screen checklist, mandatory improvement list + prioritize top 3 step
- experiment-pipeline skill: P&L chart must include SPY B&H
- experiment-checks.md: Check 7 must compute dynamically
**Commit:** 329a1f7

## Next Steps

1. **A6 FIXED.** 5/5 post-guide experiments scored 2/2 on A6.
2. **Score trend:** 26→23→23→20→23→26→27. Upward after A6 fix + audit cleanup.
3. **Remaining gap:** V2 (Check 6 equity chain independence) is the only consistent deduction on high-scoring experiments.
4. **Exp 10 (option volume ratio) running** — first experiment using flow_cache.

## Curriculum Progression

| Phase | Experiments | Status | Guardrails Exercised |
|-------|------------|--------|---------------------|
| Basic | 00-19 | NOT STARTED | pending-row, single-day queries, wall-clock, TC |
| Complement | 20-100 | — | split handling, flow aggregation, multiple signals |
| Composite | 100-186 | — | multi-leg, position overlap, PhasedDay, accounting |
| Production | 187-194 | — | all guardrails simultaneously, C-class audit |
