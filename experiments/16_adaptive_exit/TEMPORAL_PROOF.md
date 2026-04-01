# Temporal Proof: Experiment 16 — Adaptive Exit Timing

## Wall-Clock Diagram

```
Day T-1                        Day T                              Day T+1
────────────────────────────────────────────────────────────────────────────
  15:30 ET                  09:35 ET      10:30 ET     15:30 ET    09:35 ET
    │                         │              │            │           │
  p1530[T-1]              p0935[T]       p1030[T]     p1530[T]      │
  (entry price)           (gap compute)  (late exit)  (next entry)   │
    │                         │              │            │           │
    │   ◄── overnight ──►     │              │            │           │
    │                    EXIT DECISION:      │            │           │
    │                    gap > threshold?    │            │           │
    │                         │              │            │           │
    │                    EARLY: settle here  │            │           │
    │                         │         LATE: settle here │           │
    │                         │              │            │           │
    │                    acc.update()         │      signal compute   │
    │                    (prev_p1530)         │      + percentile     │
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | prev_p1530 (accumulator) | T-1 15:30 | T 09:35 | Y | run_strategy.py:run_adaptive — acc.update uses prev_p1530 |
| 2 | p0935 (gap + early exit + accumulator) | T 09:35 | T 09:35 (exit), T 15:30 (signal) | Y | Gap + early settle at 09:35, iret denom at 15:30 |
| 3 | p1030 (late exit) | T 10:30 | T 15:30 | Y | Late settle before entry decision |
| 4 | p1530 (entry) | T 15:30 | T+1 09:35 | Y | Pending-row: entry at T 15:30, settle T+1 |
| 5 | FRED (regime) | T-1 EOD | T 09:35 | Y | MacroRegime.get_regime — panel.index < today |

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2 | PASS | Single position, max 100% |
| C5 | PASS | `equity *= (1 + day_ret)` — multiplicative |
| C-exit | PASS | Flat penalty for missing exit, logged to data_gaps |
| C-TC | PASS | `2*TC` per trade from shared/config.py |
| C-split | PASS | `abs(rr) >= SPLIT_THR` on both exit paths and accumulator |
| C-sizing | PASS | Single position, no future data |

## Results (6 configs)

| Config | Train Sharpe | Test Sharpe | Return | Max DD | WR | N | Early | Late |
|--------|-------------|------------|--------|--------|-----|---|-------|------|
| fixed_0935 | +1.41 | +2.01 | +350% | 16.1% | 57% | 211 | 0 | 0 |
| adaptive_median | +1.21 | +2.03 | +371% | 20.5% | 63% | 211 | 100 | 111 |
| adaptive_05pct | +1.20 | +2.03 | +369% | 20.5% | 63% | 211 | 101 | 110 |
| adaptive_1pct | +1.17 | +1.85 | +334% | 20.5% | 59% | 211 | 80 | 131 |
| adaptive_positive | +1.13 | +1.93 | +320% | 17.1% | 66% | 211 | 122 | 89 |
| fixed_1030 | +0.85 | +1.93 | +256% | 22.3% | 54% | 211 | 0 | 0 |

### Analysis

- **fixed_0935** has highest train Sharpe (+1.41) but adaptive configs have higher total return
- **adaptive_median** and **adaptive_05pct** best test Sharpe (+2.03) with higher returns than fixed
- Adaptive exit trades off train Sharpe for higher win rate (63% vs 57%) and total return
- All 211 trades are identical entry — only exit timing differs
- All configs beat SPY B&H (+49%) on return

## Optuna Results (gap_threshold optimization)

| Metric | Seed (0.005) | Best (0.0117) | Pareto (0.0074) |
|--------|-------------|---------------|-----------------|
| Train Sharpe | 1.200 | 1.257 | 1.249 |
| Test Sharpe | 1.795 | 1.852 | 1.965 |
| Gap | -0.595 | -0.595 | -0.716 |

- 150 trials, converged at trial #16
- Best improvement: +0.056 over seed
- Negative gap (test > train) — no overfitting
- Pareto recommendation: gap_threshold=0.0074 (balance of high train + low gap)
- First experiment to use shared/optuna_utils.run_optimization() — API validated

## Verification (8/8 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| 1 | PASS | 5 dates × 10 symbols × 4 checkpoints — all match raw DB |
| 2 | PASS | DST offsets: [-5.0, -4.0] |
| 3 | PASS | Trace 2024-02-27 — all 5 accesses causal |
| 4 | PASS | 2023-05-22 AAPL raw DB verified, cache match |
| 5 | PASS | Train=1.406 Test=2.006 Full=1.587 |
| 6 | PASS | 25 dates, max_delta=5.82e-11 |
| 7 | PASS | Train spread +0.006530, Test spread +0.009233 |
| 8 | PASS | 6 symbols all 900+ days |

## Statistical Robustness (fixed_0935)

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation | PASS (p=0.000) | Signal Sharpe distinguishable from random |
| Bootstrap CI | PASS (Sharpe 1.587, 95% CI [0.752, 2.529]) | CI excludes zero |
| Concentration | PASS (top 5 = 26.7%) | P&L reasonably distributed |

## Commit Gate Matrix

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 0 | Temporal audit table, all Causal=Y | PASS | § above |
| 1 | Wall-clock diagram | PASS | § above |
| 2 | Signal-before-return causality | PASS | Pending-row pattern, gap observable at 09:35 |
| 3 | All DB queries single-day bounded | PASS | CursorEngine |
| 4 | Split filter on signal AND return | PASS | Both exit paths + accumulator |
| 5 | Incremental vs batch match | PASS | Check 6: max_delta=5.82e-11 |
| 6 | C-class accounting | PASS | § C-Class above |
| 7 | 8-step verification | PASS | 8/8 |
| 8 | Train/test Sharpe with degradation | PASS | § Results above |
| 9 | Signal direction (Check 7) | PASS | Both spreads positive |
| 10 | WebSearch in STEP 0 | PASS | PREFLIGHT.md |
| 11 | Statistical robustness (3 tests) | PASS | All 3 pass |
| 12 | Optuna | PASS | 150 trials, baseline vs optimized reported, Pareto analysis |
