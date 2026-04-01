# Pre-Flight: Experiment 14 — Capital Allocation Fix (C2)

## Literature Grounding

WebSearch: "multi-leg portfolio capital allocation position overlap constraint debit-only academic paper"

- [Portfolio Optimization with Linear and Fixed Transaction Costs](https://web.stanford.edu/~boyd/papers/pdf/portfolio_submitted.pdf) — Boyd et al. Convex optimization with leverage/margin constraints. Gross exposure ≤ 100% is standard.
- [Fifty Years of Portfolio Optimization](https://www.sciencedirect.com/science/article/pii/S0377221723009827) — Survey of cardinality-constrained portfolio optimization. Capital allocation across legs with no-leverage constraint is well-studied.
- [Portfolio Constraints (textbook)](https://portfoliooptimizationbook.com/book/6.2-portfolio-constraints.html) — Standard treatment of long-only, no-leverage, gross exposure constraints.

Capital allocation under gross exposure ≤ 100% is standard portfolio theory. The key question is empirical: does priority-based allocation (base vs gold) outperform naive splits?

---

## 1. Hypothesis

Multi-leg composite (base overnight + gold intraday + gold overnight + SQQQ spike + flow + cash sweep) has a C2 bug: base overnight and gold overnight can both enter at 100% on the same night = 200% capital deployed. Testing 6 allocation approaches to fix.

## 2. Signal type

Multi-signal composite: cross-sectional momentum (base), EMA trend (gold), institutional flow, VXX spike reversal (SQQQ).

## 3. Return target

- **Leg 1 (base overnight):** `p0935[T+1] / p1530[T] - 1` — signal + entry at 15:30, exit at 09:35 next day
- **Leg 2 (gold intraday):** `p1600[T] / p1030[T] - 1` — entry at 10:30, exit at 16:00 same day
- **Leg 3 (gold overnight):** `p0935[T+1] / p1600[T] - 1` — GLD entry at 16:00 on gap nights, exit 09:35
- **Leg 4 (SQQQ spike):** `p0935[T+1] / p1600[T] - 1` — SQQQ entry on VXX spike nights
- **Leg 5 (flow):** `p0935[T+1] / p1530[T] - 1` — flow-ranked stocks on base-idle nights
- **Cash sweep:** 3.35% APY on idle capital, pro-rated by idle fraction

## 4. Data sources

- `minute_bars` table: p0935, p1030, p1530, p1600 for all symbols
- `fred_releases` + SPY/VXX closes: MacroRegime HMM
- `flow_cache/output/`: institutional options flow parquets
- Symbols: 153 training symbols + GLD, GDX, NUGT, SPY, QQQ, VXX, SQQQ

## 5. Temporal availability

- p0935: available 09:35 ET (settle overnights, update accumulators)
- p1030: available 10:30 ET (gold intraday entry)
- p1530: available 15:30 ET (base signal, VXX spike detection)
- p1600: available 16:00 ET (gold intraday settle, overnight entries)
- FRED: fit on data strictly < today
- Flow: yesterday's institutional flow (T-1 available at T)

## 6. Split risk assessment

Yes — split filter on all legs (signal and return sides). SPLIT_THRESHOLD from config.

## 7. Expected Sharpe range

- Individual legs: +0.5 to +2.0
- Composite: +1.5 to +3.0 (diversification benefit)
- Warning bound: > 3.0 in test triggers investigation. The "bugged" config may exceed this due to 200% exposure.

## 8. Date range

2022-01-18 to 2026-02-28 (START_DATE to END_DATE from config). Reference used date.today() — converted to END_DATE for reproducibility.

## 9. Partial-period boundary check

No calendar-period signals. Monday detection uses weekday(), VXX momentum uses rolling lookback. No partial-period risk.
