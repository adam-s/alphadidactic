# Temporal Proof: 11 Flow Gap-Fill

## Wall-Clock Diagram

```
Day T:
  09:35 ET  →  Resolve p0935 for 153 symbols + SPY/QQQ/VXX
               Update Accumulator (same as base overnight — needed for idle detection)
               Update flow EMAs with T-1 data (yesterday's net_extrinsic_mm change)
               Settle pending positions

  15:30 ET  →  Resolve p1530 for all symbols
               VXX kill switch check
               Run base signal logic → determine if base would trade tonight
               If base is IDLE and regime=bull and no VXX spike:
                 Rank symbols by flow EMA value (> 0)
                 Entry: top 5 flow-positive at p1530 prices

  16:00 ET  →  Resolve p1600 (stored for next day)

Day T+1:
  09:35 ET  →  Settle: return = mean(p0935[T+1] / p1530[T] - 1 - 2*TC)
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | T-1 flow data | after close day T-1 | 09:35 day T | Y | yesterday's flow available overnight |
| 2 | prev_flow (EMA denominator) | day T-2 or earlier | 09:35 day T | Y | stored |
| 3 | p0935 + prev_p1600 (accumulator) | 09:35 day T | 09:35 day T | Y | resolve_up_to(09:35) |
| 4 | p0935 + p1530 (base idle check) | 15:30 day T | 15:30 day T | Y | resolve_up_to(15:30) |
| 5 | signal_history (base percentile gate) | before day T | 15:30 day T | Y | appended after gate |
| 6 | flow EMA values (ranking) | 09:35 day T | 15:30 day T | Y | updated before entry |
| 7 | p1530 selected stocks (entry) | 15:30 day T | 15:30 day T | Y | entry at observed price |
| 8 | p0935 (settlement) | 09:35 day T+1 | 09:35 day T+1 | Y | pending-row |

All Causal = Y.

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2: No overlap | PASS | Max 5 positions, equal weight |
| C5: Multiplicative | PASS | `equity *= (1 + day_ret)` |
| C-exit | PASS | settle_price_fallback + carry; 0 gaps |
| C-TC | PASS | `- 2*TC` per position |
| C-split | PASS | `is_split()` on signal and return |

## Results

| Period | Sharpe | Return | Max DD | Win Rate | Trades |
|--------|--------|--------|--------|----------|--------|
| Train | +1.046 | — | — | — | — |
| Test | +0.456 | — | — | — | — |
| Full | +0.793 | +40.6% | 16.6% | 51.5% | 1390 |

## Statistical Robustness

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation (TC-fair) | p=0.000 | Significant |
| Bootstrap Sharpe CI | 95% CI [-0.03, 1.79] | Barely includes zero |
| Concentration | Top 5 = 14.2% | Well distributed |

## Verification

8/8 checks passed. Check 6: 25 dates, max_delta = 2.91e-11.

**R5 fix applied:** p1600 grace=390 for half-day closes. Sharpe shifted from 1.070/0.526 to 1.046/0.456.

## Data Gaps

None.

## Note on Flow Sign Convention

`net_extrinsic_mm = put_extrinsic - call_extrinsic`. Positive = more put premium. The signal selects stocks where the EMA of daily change in net_extrinsic is positive (increasing put premium). This may function as a contrarian signal — institutional put hedging indicates long equity positions being protected, which is bullish. The convention is causal (all inputs exist before the computation) per A14 — see rules/temporal-correctness.md.
