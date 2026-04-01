# Temporal Proof: 08 Gold Overnight

## Wall-Clock Diagram

```
Day T:
  09:35 ET  →  Resolve p0935 (GLD, VXX, SPY)
               Update gold EMA with overnight return: p0935_GLD / prev_p1600_GLD - 1
               Settle any pending GLD position from T-1

  16:00 ET  →  Resolve p1600 (GLD, VXX, SPY)
               Update VXX close-to-close return (includes today)
               Compute VXX 20-day momentum (includes today's return)
               Check regime (MacroRegime, strict < today for FRED data)
               Entry conditions: regime=bull AND (monday OR vxx_momentum>=0) AND ema>0
               If conditions met → enter GLD at p1600

Day T+1:
  09:35 ET  →  Settle: return = p0935[T+1] / p1600[T] - 1 - 2*TC
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | GLD p0935 (EMA input) | 09:35 day T | 09:35 day T | Y | resolve_up_to(09:35) |
| 2 | prev_p1600 GLD (EMA input) | 16:00 day T-1 | 09:35 day T | Y | stored from previous day |
| 3 | VXX p1600 (momentum) | 16:00 day T | 16:00 day T | Y | resolve_up_to(16:00) |
| 4 | FRED panel (regime) | < day T (publication lag) | 16:00 day T | Y | MacroRegime strict filter |
| 5 | GLD p1600 (entry) | 16:00 day T | 16:00 day T | Y | resolve_up_to(16:00) |
| 6 | GLD p0935 (settlement) | 09:35 day T+1 | 09:35 day T+1 | Y | pending-row |

All Causal = Y.

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2: No overlap | PASS | Single position (GLD only) |
| C5: Multiplicative | PASS | `equity *= (1 + day_ret)` |
| C-exit | PASS | settle_price_fallback(); 0 gaps |
| C-TC | PASS | `TC = 0.0002`, applied as `- 2*TC` |
| C-split | PASS | `is_split()` on return and EMA input |

## Results

| Period | Sharpe | Return | Max DD | Win Rate | Trades |
|--------|--------|--------|--------|----------|--------|
| Train | +1.373 | — | — | — | — |
| Test | +0.692 | — | — | — | — |
| Full | +0.861 | +27.8% | 8.1% | 62.9% | 210 |

## Statistical Robustness

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation (TC-fair) | p=0.000 | Significant |
| Bootstrap Sharpe CI | 95% CI [-0.10, 1.94] | Includes zero |
| Concentration | Top 5 = 17.7% | Well distributed |

## Verification

7/8 checks passed. Check 6: 25 dates, max_delta = 5.46e-12.

**Check 7 FAIL (documented null result):** With TC-fair unconditional benchmark, test spread is -0.06% (signal +0.10%/night vs unconditional GLD overnight +0.16%/night after TC). Train spread is positive (+0.14%). The signal worked in-sample but does not persist out-of-sample. This is an honest null result, not a code bug.

**R5 fix applied:** p1600 grace window widened to 390 minutes to resolve half-day closes at 13:00 ET. Previously, 8 half-days had stale prev_p1600, contaminating the next morning's EMA input. Train Sharpe shifted from 1.429 to 1.373 after fix.

## Data Gaps

None.
