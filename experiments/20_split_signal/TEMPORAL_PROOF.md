# Temporal Proof: Experiment 20 — Split Signal

## Wall-Clock Diagram

```
Day S (split)          Day S+1                      Day S+1+N
───────────────────────────────────────────────────────────────
  09:35 ET  16:00 ET    09:35 ET       15:30 ET      15:30 ET
    │          │          │               │              │
  price jumps  │       ENTRY           daily P&L       EXIT
  3x+ on      │       GLD at open     GLD close       GLD close
  inverse ETF  │          │               │              │
    │          │          │               │              │
  SIGNAL:    store     buy GLD         hold ──────────► sell GLD
  split      p1600     at p0935        20 trading      at p1530
  detected   for       next day        days            on day N
             detection
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | Inverse ETF p1600 (split detection) | S 16:00 | S+1 09:35 | Y | Price jump observable at open next day |
| 2 | GLD p0935 (entry) | S+1 09:35 | S+1 09:35 | Y | Entry at open |
| 3 | GLD p1530 (daily P&L) | T 15:30 | T+1 09:35 | Y | Close-to-close daily return |
| 4 | GLD p1530 (exit) | S+N 15:30 | S+N 15:30 | Y | Exit at close of holding period |

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2 | PASS | Single position, 100% capital when active, 0% when idle |
| C5 | PASS | `equity *= (1 + day_ret)` — multiplicative |
| C-exit | PASS | data_gaps logged for missing exit prices |
| C-TC | PASS | 2*TC per trade, amortized over holding period |
| C-split | PASS | Split filter on GLD returns (|r| < SPLIT_THR) |
| C-sizing | PASS | Binary: all-in or cash, no future data |

## Results (5 configs)

| Config | Train | Test | Return | DD | WR | N |
|--------|-------|------|--------|-----|-----|---|
| gld_20d | +0.80 | +1.01 | +30% | 9.7% | 53% | 10 |
| gld_40d | +0.79 | +0.84 | +36% | 12.1% | 52% | 9 |
| short_vxx_20d | +0.13 | +1.20 | +29% | 47.9% | 58% | 10 |
| short_vxx_40d | -0.12 | -0.23 | -43% | 60.8% | 56% | 9 |
| spy_20d | +0.08 | +1.70 | +10% | 16.0% | 55% | 10 |

### Analysis

- **gld_20d** is the most robust: positive in both train (+0.80) and test (+1.01), lowest DD (9.7%)
- **spy_20d** has highest test Sharpe (+1.70) but near-zero train (+0.08) — less reliable
- **short_vxx_40d** fails — VXX is too volatile for 40-day shorts
- Event-driven: mostly cash, ~10 trades over 4 years. Equity curve is flat with steps.

### Deep analysis findings (from multi-perspective study)

- GLD 20d post-split: +2.69% mean, t=+2.10 (statistically significant)
- Short VXX 40d: +3.43% mean, t=+2.08 (significant in event study, but strategy DD too high)
- VOL splits → SPY 20d: +3.21%, 100% win rate (5/5 events)
- Splits mark vol PEAKS, not precursors — gold benefits from flight-to-safety unwind

## Verification (8/8 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| 1 | PASS | 5 dates × 6 symbols × 3 checkpoints |
| 2 | PASS | DST [-5.0, -4.0] |
| 3 | PASS | Trace 2024-02-23, 2 accesses causal |
| 4 | PASS | GLD raw DB verified, cache match |
| 5 | PASS | Train=0.800 Test=1.015 |
| 6 | PASS | 25 dates, max_delta=1.27e-11 |
| 7 | PASS | Both spreads positive |
| 8 | PASS | 6 symbols 900+ days |

## Statistical Robustness (gld_20d)

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation | PASS (p=0.024) | Significant at 5% level |
| Bootstrap CI | FAIL (CI [-0.067, 1.948]) | Marginally includes zero |
| Concentration | PASS (top 5 = 20.0%) | Well distributed |

## Commit Gate Matrix

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 0 | Temporal audit table | PASS | § above |
| 1 | Wall-clock diagram | PASS | § above |
| 2 | Signal-before-return | PASS | Split detected day S, entry day S+1 |
| 3 | Single-day queries | PASS | CursorEngine |
| 4 | Split filter both sides | PASS | Signal is the split itself; return filtered |
| 5 | Incremental vs batch | PASS | Check 6: 1.27e-11 |
| 6 | C-class accounting | PASS | § C-Class above |
| 7 | 8-step verification | PASS | 8/8 |
| 8 | Train/test with degradation | PASS | § Results above |
| 9 | Signal direction | PASS | Both spreads positive |
| 10 | WebSearch | PASS | PREFLIGHT.md |
| 11 | Statistical robustness | PARTIAL | Permutation PASS, Bootstrap marginal |
| 12 | Optuna | SKIP | Event-driven with ~10 events — no parameter to optimize |
