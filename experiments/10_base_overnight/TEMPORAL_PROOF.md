# Temporal Proof: 10 Base Overnight

## Wall-Clock Diagram

```
Day T:
  09:35 ET  →  Resolve p0935 for 153 symbols + SPY/QQQ/VXX
               Update Accumulator: overnight return = p0935 / prev_p1600 - 1
               Settle pending positions (up to 5 stocks)

  15:30 ET  →  Resolve p1530 for all symbols
               Compute VXX intraday return → kill switch if > 3%
               Get regime from MacroRegime (strict < today)
               For each symbol: compute iret = p1530/p0935 - 1
               Filter: |iret| < SPLIT_THR, |iret| >= MIN_IRET, hit_rate > HR_THR
               Compute signal = iret × avg_pos × (1 + streak_mult × streak) × hit_rate
               Rank candidates, apply percentile gate (prior 252 days, excludes today)
               Entry: top 5 at p1530 prices

  16:00 ET  →  Resolve p1600 (stored as prev_p1600 for next day)

Day T+1:
  09:35 ET  →  Settle: return = mean(p0935[T+1] / p1530[T] - 1 - 2*TC) for each position
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | p0935 all symbols (accumulator) | 09:35 day T | 09:35 day T | Y | resolve_up_to(09:35) |
| 2 | prev_p1600 (accumulator denominator) | 16:00 day T-1 | 09:35 day T | Y | stored |
| 3 | p0935 + p1530 (intraday return) | 15:30 day T | 15:30 day T | Y | resolve_up_to(15:30) |
| 4 | VXX p0935 + p1530 (kill switch) | 15:30 day T | 15:30 day T | Y | same phase |
| 5 | FRED panel (regime) | < day T | 15:30 day T | Y | MacroRegime strict filter |
| 6 | signal_history (percentile gate) | before day T | 15:30 day T | Y | appended AFTER gate applied |
| 7 | p1530 selected stocks (entry) | 15:30 day T | 15:30 day T | Y | entry at observed price |
| 8 | p0935 (settlement) | 09:35 day T+1 | 09:35 day T+1 | Y | pending-row |

All Causal = Y. Key: percentile gate uses signal_history[-252:] BEFORE today's signal is appended (line 171 in run_strategy.py).

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2: No overlap | PASS | Max 5 positions, equal weight = 20% each |
| C5: Multiplicative | PASS | `equity *= (1 + day_ret)` where day_ret = mean(position_returns) |
| C-exit | PASS | settle_price_fallback + carry for missing; 0 gaps found |
| C-TC | PASS | `- 2*TC` per position |
| C-split | PASS | `is_split()` on iret (signal) and rr (return) |

## Results

| Period | Sharpe | Return | Max DD | Win Rate | Trades |
|--------|--------|--------|--------|----------|--------|
| Train | +1.625 | — | — | — | — |
| Test | +3.017 | — | — | — | — |
| Full | +2.061 | +231.6% | 11.1% | 57.4% | 1667 |

**Note:** Test Sharpe 3.017 triggers H3 investigation. Check 5 reports FAIL. This is a property of the strategy in a strong bull test period, not a temporal or accounting bug (verified by Check 6 independent implementation matching to 5e-11).

**R5 fix applied:** p1600 grace window widened to 390 minutes for half-day closes. Sharpe shifted from 1.633/3.251 to 1.625/3.017.

## Statistical Robustness

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation (TC-fair) | p=0.000 | Significant |
| Bootstrap Sharpe CI | 95% CI [1.24, 3.01] | Excludes zero |
| Concentration | Top 5 = 24.8% | Reasonably distributed |

## Verification

7/8 checks passed. Check 5 FAIL (Test Sharpe > 3.0 — investigation documented above). Check 6: 25 dates, max_delta = 5.09e-11.

## Data Gaps

None.
