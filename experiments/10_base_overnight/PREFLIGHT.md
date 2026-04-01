# Pre-Flight: 10 Base Overnight

## 1. Hypothesis
Cross-sectional stock selection using intraday momentum, overnight hit rate, and win streaks predicts overnight returns. Regime-gated (bull only), VXX kill switch.

## 2. Signal Type
Cross-sectional composite: `intraday_return × avg_positive_overnight × (1 + streak_mult × streak) × hit_rate`. Ranked, top 5 enter.

## 3. Return Target
`p0935[T+1] / p1530[T] - 1` — overnight return on selected stocks. Entry at 15:30, exit at 09:35 next open.

## 4. Data Sources
- `minute_bars`: 153-symbol universe + SPY, QQQ, VXX
- `msvar_panel.parquet`: FRED macro features for HMM regime
- `symbol_universe.json`: cached universe (153 symbols, 90%+ SPY coverage)

## 5. Temporal Availability
- p0935: available 09:35 ET (accumulator update, settlement)
- p1530: available 15:30 ET (signal computation, entry)
- p1600: available 16:00 ET (close prices for next day's accumulator)
- FRED: publication lag respected

## 6. Split Risk
153 symbols — multiple splits in range (AMZN, GOOG, TSLA, NVDA, etc.). Magnitude filter on both signal (iret) and return sides. Authoritative split records in shared/split_adjustments.py.

## 7. Expected Sharpe Range
High (1.0 to 2.5). Cross-sectional selection from large universe. Test Sharpe > 3.0 flagged by H3 — investigate but may be legitimate in strong bull market test period.

## 8. Date Range
2022-01-18 to 2026-02-28. Train through 2024-12-31.

## 9. Boundary Check
Accumulator needs 20-day warmup. Percentile gate needs 60-day history. First ~60 trading days are warmup.
