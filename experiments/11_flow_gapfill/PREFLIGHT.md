# Pre-Flight: 11 Flow Gap-Fill

## 1. Hypothesis
Institutional options flow (block/mega/large sized trades) predicts overnight stock returns on nights when the base overnight strategy is idle.

## 2. Signal Type
Options flow (EMA-10 of daily change in net_extrinsic_mm for institutional size buckets). Cross-sectional ranking, top 5 enter.

## 3. Return Target
`p0935[T+1] / p1530[T] - 1` — overnight return on flow-selected stocks.

## 4. Data Sources
- `minute_bars`: 153-symbol universe + SPY, QQQ, VXX
- `flow_cache/output/`: per-symbol parquets with institutional flow decomposition
- `msvar_panel.parquet`: FRED macro for regime gate

## 5. Temporal Availability
- Flow data: T-1 flow updated at 09:35 day T (fully observable — yesterday's after-hours data)
- Price checkpoints: p0935, p1530, p1600 at their respective times
- FRED: publication lag respected

## 6. Split Risk
Same 153-symbol universe as base overnight. Magnitude filter on both sides.

## 7. Expected Sharpe Range
Low-moderate (0.3 to 1.0). Flow signal is complementary — only trades when base is idle, so lower opportunity set.

## 8. Date Range
2022-01-18 to 2026-02-28. Train through 2024-12-31.

## 9. Boundary Check
Flow EMA needs 5-day warmup. Base accumulator needs 20-day warmup (shared logic for idle detection). Flow cache covers 109 of 153 symbols.
