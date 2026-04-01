# Temporal Proof: Experiment 19 — Gap Reversal (Original Design)

## Hypothesis & Finding

**Original hypothesis:** Stocks with large negative overnight gaps mean-revert during the day.
**Result:** REJECTED. All reversal configs have deeply negative Sharpe (-0.83 to -1.42).

**Discovery:** The INVERSE works — gap-UP stocks continue rising (momentum continuation). top3_inv has Test +1.30. But train is near zero (-0.07), bootstrap CI includes zero. This is a marginal/null result — the signal may not be reliable.

## Results (9 configs)

| Config | Train | Test | Return | DD | WR | N |
|--------|-------|------|--------|-----|-----|---|
| top3_long (reversal) | -0.99 | -2.02 | -91% | 91.8% | 46% | 3055 |
| top3_longshort | -1.20 | -3.17 | -84% | 84.9% | 45% | 6103 |
| **top3_inv** (momentum) | **-0.07** | **+1.30** | +4% | 54.1% | 50% | 3048 |
| top3_inv_exit1030 | -0.74 | +0.45 | -50% | 68.8% | 48% | 3081 |
| top3_inv_exit1100 | -0.66 | +0.93 | -45% | 71.1% | 48% | 3081 |
| top3_inv_exit1200 | -0.51 | +1.20 | -38% | 70.0% | 49% | 3079 |
| top3_inv_close (p1600 gap) | -0.32 | +1.17 | -27% | 61.8% | 50% | 3078 |

## Key findings

1. **Gap reversal is anti-alpha** in our universe/period — momentum continuation dominates
2. **Later exit is better** — the momentum effect builds through the day (1030 < 1100 < 1200 < 1530)
3. **p1530 gap base better than p1600** — 15:30 "close" captures more relevant information
4. **Honest null**: train Sharpe near zero, bootstrap CI includes zero. Test-only signal.

## Verification (7/8)

Check 7 FAIL: train signal doesn't beat SPY B&H (spread -0.000453). This documents the null — correct behavior.

## Statistical Robustness (top3_inv)

- Permutation: PASS (p=0.000)
- Bootstrap: FAIL (CI [-0.767, 1.215] includes zero)
- Concentration: PASS
