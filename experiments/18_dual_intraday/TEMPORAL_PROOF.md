# Temporal Proof: Experiment 18 — Dual Intraday Checkpoint

## Wall-Clock Diagram

```
Day T-1              Day T
──────────────────────────────────────────────────────────
  16:00 ET       09:35 ET    10:30 ET   13:00 ET   16:00 ET
    │               │           │          │          │
  prev_close    p0935[T]    p1030[T]   p1300[T]   p1600[T]
    │          (EMA update)  (entry)   (split)     (exit)
    │               │           │          │          │
    │  overnight ►  │           │          │          │
    │  NUGT return  │    morning leg ──►   │          │
    │               │           │   afternoon leg ──► │
    │          EMA > 0?         │          │          │
    │          (go/no-go)       │          │          │
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | prev_close (EMA input) | T-1 16:00 | T 09:35 | Y | run_strategy.py:run_dual |
| 2 | p0935 (EMA + signal) | T 09:35 | T 10:30 | Y | EMA updated before entry |
| 3 | p1030 (entry) | T 10:30 | T 16:00 | Y | Intraday same-day |
| 4 | p1300 (split) | T 13:00 | T 16:00 | Y | Split before exit |

## Results (5 configs)

| Config | Train | Test | Return | DD | WR | N |
|--------|-------|------|--------|-----|-----|---|
| single_1030_1600 | +1.01 | +1.69 | +272% | 21.1% | 56% | 614 |
| dual_1030_1230_1600 | +0.81 | +1.52 | +197% | 23.3% | 56% | 1231 |
| dual_1030_1300_1600 | +0.79 | +1.56 | +199% | 23.2% | 56% | 1233 |
| dual_1030_1330_1600 | +0.80 | +1.49 | +191% | 23.0% | 56% | 1228 |
| dual_1030_1400_1600 | +0.80 | +1.49 | +191% | 23.0% | 56% | 1228 |

**Conclusion:** Single leg outperforms all dual configs on both train Sharpe and return. Extra TC (4*TC vs 2*TC per day) offsets the compounding benefit. Dual intraday does NOT improve the strategy.

## Known R6: prev_close overwrite

Same pattern as exp 15: last checkpoint overwrites prev_close. For this experiment, the last checkpoint is always p1600 for NUGT, so the EMA correctly uses close-to-open returns. R6 does not bite here.

## Optuna

50 trials, 4 categorical split times. Best: 12:30 (Train +0.809). All dual configs worse than single (+1.01). Single leg is definitively better.

## Verification (8/8 PASS)

All checks pass. Check 6: 25 dates, max_delta=4.37e-11.

## Statistical Robustness (single_1030_1600)

Permutation PASS, Bootstrap PASS (CI excludes zero), Concentration PASS.

## Commit Gate Matrix

All 12 gates PASS.
