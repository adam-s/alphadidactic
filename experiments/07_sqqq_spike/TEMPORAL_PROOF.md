# Temporal Proof: 07 SQQQ Spike

## Wall-Clock Diagram

```
Day T:
  09:35 ET  →  Resolve p0935 (VXX, SQQQ, SPY)
               IF pending SQQQ position from T-1:
                 exit_price = p0935 SQQQ
                 IF exit_price missing → settle_price_fallback (search full day)
                 IF exit_price found → return = exit/entry - 1 - 2*TC
                 IF split detected (|return| >= 20%) → return = 0%
                 Record: n_trades++, equity *= (1 + day_ret)
               ELSE:
                 day_ret = 0.0 (no position to settle)

  15:30 ET  →  Resolve p1530 (VXX)
               vxx_intraday = p1530_VXX / p0935_VXX - 1
               IF vxx_intraday > 0.03 → vxx_spike = True
               ELSE → vxx_spike = False (no entry tonight)

  16:00 ET  →  Resolve p1600 (SQQQ, SPY)
               IF vxx_spike AND no pending position:
                 IF SQQQ p1600 available → pending = (SQQQ, p1600, today)
               ELSE:
                 No entry (most nights — signal triggers ~12% of days)
               Store prev_p1600 for SPY benchmark

Day T+1:
  09:35 ET  →  Settlement of T's position (see 09:35 block above)
```

Decision at 16:00 day T. Return settles at 09:35 day T+1. Signal (VXX spike) observed at 15:30, strictly before entry. ~88% of nights: no spike, no entry, day_ret = 0.

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | VXX p0935 | 09:35 day T | 09:35 day T (spike denominator) | Y | resolve_up_to(09:35) |
| 2 | VXX p1530 | 15:30 day T | 15:30 day T (spike numerator) | Y | resolve_up_to(15:30) |
| 3 | SQQQ p1600 | 16:00 day T | 16:00 day T (entry price) | Y | resolve_up_to(16:00) |
| 4 | SQQQ p0935 | 09:35 day T+1 | 09:35 day T+1 (settlement) | Y | pending-row: entry_date < today |

All Causal = Y.

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2: No overlap | PASS | Single position (SQQQ only), max 1 pending |
| C5: Multiplicative | PASS | `equity *= (1 + day_ret)` |
| C-exit: Missing price | PASS | settle_price_fallback() searches full day; 0 gaps found |
| C-TC: From config | PASS | `TC = 0.0002` from shared/config.py, applied as `- 2*TC` |
| C-split: Both sides | PASS | `is_split()` on return; signal uses ratio (VXX intraday) not raw prices |

## Results

| Period | Sharpe | Return | Max DD | Win Rate | Trades |
|--------|--------|--------|--------|----------|--------|
| Train (2022-01 to 2024-12) | +0.495 | — | — | — | — |
| Test (2025-01 to 2026-02) | +1.177 | — | — | — | — |
| Full | +0.693 | +55.9% | 16.6% | 52.7% | 129 |

## Statistical Robustness

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation (TC-fair) | p=0.000 | Signal distinguishable from random day selection |
| Bootstrap Sharpe CI | 95% CI [-0.24, 1.57] | CI includes zero — not distinguishable from noise |
| Concentration | Top 5 = 66% of P&L | Highly concentrated — result driven by few spike events |

The signal is statistically significant by permutation but the Sharpe CI includes zero and the return is concentrated in a few large spike events (notably April 2025 tariff panic). This is consistent with the nature of the signal — VXX spikes are rare, high-impact events.

## Verification

All 8 checks passed.

- Check 6 (incremental vs batch): 25 dates, max_delta = 7.28e-12
- Check 7 (signal direction): signal mean +0.26%/day vs unconditional SQQQ overnight -0.01%/day (train), +0.81% vs -0.07% (test). Benchmark is unconditional SQQQ overnight every night, not all-days mean including zeros.

## Data Gaps

None.

## Commit Gate

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 0 | Temporal audit all Causal=Y | PASS | Table above |
| 1 | Wall-clock diagram | PASS | Above |
| 2 | Signal-before-return | PASS | Pending-row: entry T, settle T+1 |
| 5 | Incremental vs batch | PASS | Check 6: max_delta 7.28e-12 |
| 6 | C-class checklist | PASS | Table above |
| 7 | 8-step verification | PASS | 8/8 |
| 8 | Train/test Sharpe | PASS | Train 0.50, Test 1.18 |
| 11 | Statistical robustness | PASS | 3 tests run |
