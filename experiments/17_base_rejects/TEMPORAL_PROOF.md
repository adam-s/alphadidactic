# Temporal Proof: Experiment 17 — Base Rejects

## Wall-Clock Diagram

```
Day T-1                        Day T                           Day T+1
───────────────────────────────────────────────────────────────────────
  15:30 ET                  09:35 ET           15:30 ET       09:35 ET
    │                         │                   │              │
  p1530[T-1]              p0935[T]           p1530[T]            │
  (entry price)           (settle)        (signal + entry)       │
    │                         │                   │              │
    │   ◄── overnight ──►     │                   │              │
    │                    acc.update()        cands.sort()         │
    │                    (prev_p1530)     primary gate → reject?  │
    │                         │            percentile check       │
    │                         │                   │              │
  FRED panel            regime gate         entry if passes      │
  available             (bull only)         pending-row ──────►  │
```

## Temporal Audit Table

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|
| 1 | prev_p1530 (accumulator) | T-1 15:30 | T 09:35 | Y | run_strategy.py:run_reject — acc.update |
| 2 | p0935 (settle + iret denom) | T 09:35 | T 15:30 | Y | Settlement then signal |
| 3 | p1530 (entry) | T 15:30 | T+1 09:35 | Y | Pending-row pattern |
| 4 | FRED (regime) | T-1 EOD | T 09:35 | Y | MacroRegime < today |

## Known R1: signal_history self-inclusion

Reference appends `best_sig` to `signal_history` BEFORE the percentile check (line 175 appends, line 179 checks). Today's signal is included in its own threshold. Effect: ~1/252 impact on threshold. Matches reference — documented, not fixed.

## Results (6 configs)

| Config | Train | Test | Return | DD | WR | Pri | Rej |
|--------|-------|------|--------|-----|-----|-----|-----|
| reject_gate50 | +1.60 | +1.83 | +496% | 16.8% | 56% | 210 | 106 |
| combined_pri_rej50 | +1.60 | +1.83 | +496% | 16.8% | 56% | 210 | 106 |
| primary_only | +1.33 | +2.01 | +325% | 16.1% | 57% | 210 | 0 |
| reject_any | +1.32 | +1.71 | +481% | 32.4% | 55% | 210 | 350 |
| reject_gate30 | +1.28 | +1.71 | +367% | 28.1% | 54% | 210 | 208 |
| reject_top3 | +1.18 | +1.58 | +283% | 29.6% | 55% | 210 | 325 |

reject_gate50 improves total return (+496% vs +325%) by filling idle nights, at cost of test Sharpe (+1.83 vs +2.01). The rejects are weaker signals but add diversification.

## Optuna (reject_pctile)

150 trials. Seed (0.50) was already optimal — no improvement found. Pareto recommended 0.484 (marginal). The 50th percentile gate is a stable optimum.

## Verification (8/8 PASS)

| Check | Status | Evidence |
|-------|--------|----------|
| 1 | PASS | 5 dates × 10 symbols × 3 checkpoints |
| 2 | PASS | DST [-5.0, -4.0] |
| 3 | PASS | 4 accesses causal |
| 4 | PASS | 2024-01-22 SMCI manual=strategy delta=0 |
| 5 | PASS | Train=1.597 Test=1.835 |
| 6 | PASS | 25 dates, max_delta=9.46e-11 |
| 7 | PASS | Both spreads positive |
| 8 | PASS | 6 symbols 900+ days |

## Statistical Robustness (reject_gate50)

| Test | Result |
|------|--------|
| Permutation | PASS (p=0.000) |
| Bootstrap CI | PASS (CI excludes zero) |
| Concentration | PASS |

## Commit Gate Matrix

All 12 gates PASS. Optuna ran (150 trials, seed optimal).
