# Pre-Flight: Experiment 13 — Regime Robustness

## Literature Grounding

WebSearch: "HMM regime switching overnight momentum equity strategy out of sample academic paper"

- [Regime-Switching Factor Investing with Hidden Markov Models](https://www.mdpi.com/1911-8074/13/12/311) — HMM regime-switching outperforms static strategies OOS in global equity. Sharpe improvements modest (0.1–0.3 delta). 2-state HMM standard.
- [A regime-switching model of stock returns with momentum and mean reversion](https://www.sciencedirect.com/science/article/pii/S0264999323000494) — Momentum works in bull regimes, mean-reversion in bear. Regime identification is the hard part.
- [Hidden Markov Models Applied To Intraday Momentum Trading](https://arxiv.org/pdf/2006.08307) — HMM applied to intraday momentum. OOS degradation common. Effect sizes small (Sharpe < 1.0 typical).
- [Regime-Aware Asset Allocation: Statistical Jump Model](https://arxiv.org/html/2402.05272v2) — Compares jump models vs HMM. Both show OOS degradation. Transaction costs matter.

Statistical benchmarks from literature: Sharpe 0.5–1.5 for regime-filtered momentum (in-sample), significant OOS decay typical. Effect sizes reported as Newey-West t-stats 1.5–2.5.

---

## 1. Hypothesis

OOS overnight momentum collapsed on 324 R1000 symbols (Train -0.30, Test -0.73) while working on 153 training symbols (Train 2.17, Test 2.09). Three hypotheses:
1. **WARMUP:** OOS symbols need more accumulator history before trading
2. **BREADTH:** Cross-sectional breadth filters out bad market days
3. **TIGHT REGIME:** Stricter HMM bull probability bounds (0.95/0.99)

## 2. Signal type

Cross-sectional overnight momentum with HMM regime gate. Accumulator-based (hit rate, avg positive return, streak).

## 3. Return target

`p0935[T] / p1530[T-1] - 1` — overnight return from 15:30 close to 09:35 open next day. Entry at p1530, exit at p0935 next day.

## 4. Data sources

- `minute_bars` table: p0935, p1530 checkpoints for all symbols
- `fred_releases` + SPY/VXX closes: MacroRegime HMM (T10Y2Y_zscore, HY_zscore, SPY_ret, VXX_ret)
- Symbol universes: 153 training symbols (shared/cache/symbol_universe.json), 324 OOS symbols (hardcoded in experiment)

## 5. Temporal availability

- p0935: available at 09:35 ET on trade date T
- p1530: available at 15:30 ET on trade date T
- FRED data: available T+1 business day (used with strict `< today` filter)
- HMM regime: fit on data strictly before today

## 6. Split risk assessment

Yes — split filter applied on both signal side (accumulator update) and return side (overnight return settlement). Threshold from shared/config.py (0.20).

## 7. Expected Sharpe range

- Training symbols: +1.0 to +2.5 (known working signal)
- OOS symbols: -1.0 to +0.5 (diagnostic — expected null/negative)
- Warning bound: > 3.0 in test triggers investigation

## 8. Date range

START_DATE through END_DATE from shared/config.py (2022-01-18 to 2026-02-28). TRAIN_END = 2024-12-31.

## 9. Partial-period boundary check

No calendar-period signals used. Accumulator lookback (80 days) is rolling, not calendar-aligned. No partial-period risk.
