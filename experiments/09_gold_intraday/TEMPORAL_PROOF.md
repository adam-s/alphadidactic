# Temporal Proof: 09 Gold Intraday

## Wall-Clock Diagram

```
Day T:
  09:35 ET  →  Resolve p0935 (GLD, GDX, NUGT, SPY)
               Update overnight EMAs: p0935 / prev_p1600 - 1 for each gold symbol

  10:30 ET  →  Resolve p1030 (NUGT)
               If GLD EMA-34 > 0 → enter NUGT at p1030

  16:00 ET  →  Resolve p1600 (NUGT, SPY)
               Settle intraday: return = p1600_NUGT / p1030_NUGT - 1 - 2*TC
```

No pending-row needed — entry and exit are same day, exit strictly after entry.

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | GLD prev_p1600 (EMA input) | 16:00 day T-1 | 09:35 day T | Y | stored |
| 2 | GLD p0935 (EMA input) | 09:35 day T | 09:35 day T | Y | resolve_up_to(09:35) |
| 3 | NUGT p1030 (entry) | 10:30 day T | 10:30 day T | Y | resolve_up_to(10:30) |
| 4 | NUGT p1600 (exit) | 16:00 day T | 16:00 day T | Y | resolve_up_to(16:00) |

All Causal = Y. Signal computed at 09:35, entry at 10:30 (55 min delay), exit at 16:00.

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2: No overlap | PASS | Single NUGT position |
| C5: Multiplicative | PASS | `equity *= (1 + day_ret)` |
| C-exit | PASS | settle_price_fallback(); 8 half-day gaps resolved at 13:00 |
| C-TC | PASS | `- 2*TC` per trade |
| C-split | PASS | `is_split()` on both EMA input and return |

## Results

| Period | Sharpe | Return | Max DD | Win Rate | Trades |
|--------|--------|--------|--------|----------|--------|
| Train | +1.037 | — | — | — | — |
| Test | +1.358 | — | — | — | — |
| Full | +1.134 | +265.4% | 24.0% | 54.7% | 749 |

## Statistical Robustness

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation (TC-fair) | p=0.012 | Significant |
| Bootstrap Sharpe CI | 95% CI [0.14, 2.06] | Excludes zero |
| Concentration | Top 5 = 27.6% | Reasonably distributed |

Note: despite strong permutation/bootstrap results, Check 7 shows the signal does not outperform unconditional NUGT intraday in the test period (spread -0.02%). The 265% return and 1.358 test Sharpe are primarily 3x leveraged gold exposure during a gold bull market, not signal alpha. The EMA filter is active 72% of days — near-permanent long with occasional pauses.

## Verification

7/8 checks passed. Check 6: 25 dates, max_delta = 5.82e-11.

**Check 7 FAIL (documented null result):** Test spread = -0.02% (signal +0.261%/day vs unconditional NUGT intraday +0.282%/day after TC). Train spread positive (+0.15%). Signal adds value in-sample but is marginally negative out-of-sample against unconditional NUGT intraday buy-and-hold.

**R5 fix applied:** p1600 grace window widened to 390 minutes for half-day closes. Train Sharpe shifted from 0.920 to 1.037 — stale prev_p1600 was corrupting EMA inputs on 8 half-days.

## Data Gaps

8 half-day closes (holidays): 2022-11-25, 2023-07-03, 2023-11-24, 2024-07-03, 2024-11-29, 2024-12-24, 2025-11-28, 2025-12-24. All resolved at 13:00 ET via settle_price_fallback (same_day_carry).
