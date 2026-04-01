# Temporal Proof: Experiment 13 — Regime Robustness

## Wall-Clock Diagram

```
Day T-1                          Day T                              Day T+1
─────────────────────────────────────────────────────────────────────────────
     15:30 ET                 09:35 ET         15:30 ET           09:35 ET
       │                        │                 │                  │
  p1530[T-1]               p0935[T]          p1530[T]          p0935[T+1]
  (entry price)         (settle prev)     (signal+entry)     (settle today's)
       │                        │                 │                  │
       │   ◄── overnight ──►    │                 │                  │
       │      return = p0935[T]/p1530[T-1] - 1    │                  │
       │                        │                 │                  │
       │                  acc.update()      signal compute            │
       │                  (causal: uses    (causal: uses              │
       │                   prev_p1530)      p0935,p1530)             │
       │                        │                 │                  │
  FRED panel              FRED panel        regime gate              │
  available               fit on <T         bull_prob <T             │
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | prev_p1530 (accumulator input) | T-1 15:30 | T 09:35 | Y | run_strategy.py:run_config — acc.update uses prev_p1530 |
| 2 | p0935 (iret denominator) | T 09:35 | T 15:30 | Y | run_strategy.py:run_config — iret = p1530/p0935 - 1 |
| 3 | p1530 (signal + entry price) | T 15:30 | T 15:30 | Y | run_strategy.py:run_config — signal computed from p1530 |
| 4 | FRED panel (regime HMM) | T-1 18:00 | T 15:30 | Y | PartialRegime.get_bull_prob — panel.index < today |
| 5 | p0935 (settlement) | T+1 09:35 | T+1 09:35 | Y | Pending-row pattern: decision at T 15:30, settle at T+1 09:35 |

## C-Class Checklist

| Check | Status | Evidence |
|-------|--------|----------|
| C2 | PASS | Single position (top-1 candidate), 100% capital max |
| C5 | PASS | `pv *= (1 + day_ret)` — multiplicative compounding in run_config() |
| C-exit | PASS | Reference uses `day_ret = -SPLIT_THR - 2*TC` for missing exit (conservative penalty) |
| C-TC | PASS | `2*TC` per trade (nightly turnover, Case 1) from shared/config.py |
| C-split | PASS | `abs(r) < SPLIT_THR` on accumulator update AND `abs(rr) >= SPLIT_THR` on settlement |
| C-sizing | PASS | Single position, no position sizing from future data |

## Results (12 configs)

### OOS (324 symbols)

| Config | Train Sharpe | Test Sharpe | Return | Max DD | WR | N |
|--------|-------------|------------|--------|--------|-----|---|
| oos_baseline | -0.47 | -0.73 | -43% | 54.1% | 46% | 428 |
| oos_warmup60 | -0.47 | -0.73 | -43% | 54.1% | 46% | 428 |
| oos_warmup120 | -0.47 | -0.73 | -43% | 54.1% | 46% | 428 |
| oos_warmup200 | -0.42 | -0.73 | -40% | 55.0% | 46% | 398 |
| oos_breadth50 | +0.29 | -0.86 | -15% | 35.4% | 49% | 251 |
| oos_breadth55 | -0.28 | -1.58 | -43% | 48.8% | 47% | 218 |
| oos_regime99 | -0.09 | -0.73 | -27% | 48.9% | 47% | 418 |
| oos_regime95 | -0.51 | -0.73 | -44% | 55.4% | 46% | 426 |
| oos_combined | +0.27 | -0.86 | -15% | 36.1% | 48% | 248 |

### Training Control (153 symbols)

| Config | Train Sharpe | Test Sharpe | Return | Max DD | WR | N |
|--------|-------------|------------|--------|--------|-----|---|
| train_baseline | +1.39 | +2.03 | +622% | 33.3% | 54% | 431 |
| train_regime95 | +1.31 | +2.11 | +576% | 33.4% | 54% | 425 |
| train_breadth50 | +1.56 | +1.17 | +302% | 27.7% | 57% | 244 |

### Degradation Analysis

- **Warmup hypothesis: REJECTED.** oos_warmup60/120 identical to baseline. warmup200 slightly better train (-0.42 vs -0.47) but identical test (-0.73). Warmup does not explain OOS collapse.
- **Breadth hypothesis: MIXED.** oos_breadth50 turns train positive (+0.29) but worsens test (-0.86 vs -0.73). Filters bad days in-sample but doesn't generalize.
- **Tight regime hypothesis: REJECTED.** oos_regime95 worse than baseline. oos_regime99 slightly better train (-0.09) but same test (-0.73).
- **Combined: REJECTED.** oos_combined matches breadth50 — regime gate adds nothing.

**Conclusion:** The OOS collapse is fundamental to the symbol universe, not fixable by warmup, breadth, or regime filters. The signal works on training symbols (153) but not on the 324 OOS R1000 symbols. This is a classic overfitting signature — the "alpha" is specific to the training universe.

## Verification (8/8 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| 1 | PASS | 5 dates × 10 symbols × 2 checkpoints — all match raw DB |
| 2 | PASS | DST offsets: [-5.0, -4.0] |
| 3 | PASS | Trace 2024-06-06 — all 5 accesses causal |
| 4 | PASS | 2023-12-15 BA: manual=0.01512156 strategy=0.01512156 delta=0.00e+00 |
| 5 | PASS | Train=1.394 Test=2.027 Full=1.591 |
| 6 | PASS | 25 dates, max_delta=2.91e-11 |
| 7 | PASS | Train spread=+0.004087, Test spread=+0.005964 (train_baseline beats SPY B&H) |
| 8 | PASS | 6 symbols all 900+ days |

## Statistical Robustness (train_baseline)

| Test | Result | Interpretation |
|------|--------|---------------|
| Permutation | PASS (p=0.000) | Signal Sharpe distinguishable from random |
| Bootstrap CI | PASS (Sharpe 1.591, 95% CI [0.734, 2.414]) | CI excludes zero |
| Concentration | PASS (top 5 = 34.6%) | P&L reasonably distributed |

## Commit Gate Matrix

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 0 | Temporal audit table, all Causal=Y | PASS | § Temporal Audit Table above |
| 1 | Wall-clock diagram | PASS | § Wall-Clock Diagram above |
| 2 | Signal-before-return causality | PASS | run_strategy.py:run_config — pending-row pattern |
| 3 | All DB queries single-day bounded | PASS | CursorEngine enforces single-day resolution |
| 4 | Split filter on signal AND return | PASS | run_config: abs(r) < SPLIT_THR on both sides |
| 5 | Incremental vs batch match | PASS | Check 6: 25 dates, max_delta=2.91e-11 |
| 6 | C-class accounting | PASS | § C-Class Checklist above |
| 7 | 8-step verification | PASS | 8/8 checks pass |
| 8 | Train/test Sharpe with degradation | PASS | § Results + Degradation Analysis above |
| 9 | Signal direction (Check 7) | PASS | Train spread +0.004087, Test spread +0.005964 |
| 10 | WebSearch in STEP 0 | PASS | PREFLIGHT.md § Literature Grounding |
| 11 | Statistical robustness (3 tests) | PASS | § Statistical Robustness above |
| 12 | Optuna | PENDING | Diagnostic experiment — see note below |

**Optuna note:** This is a diagnostic experiment testing 3 hypotheses across 12 configs. The primary signal parameters (STREAK, HR_THR, LB, PCTILE, MIN_IRET) are inherited from the base overnight strategy and held constant across all configs to isolate the regime/warmup/breadth variables. Optuna optimization of these base params was already done in the original experiments. Running Optuna on the regime params (regime_lo, warmup_days, breadth_thr) would be searching over the 12-config grid that was already exhaustively tested.
