# Pre-Flight: 07 SQQQ Spike

## 1. Hypothesis
VXX intraday spikes (>3%) predict SQQQ overnight gains. When volatility spikes during the trading day, the subsequent overnight session tends to see continued downward pressure on equities (benefiting inverse ETFs).

## 2. Signal Type
Price-based volatility indicator (VXX intraday return as proxy for market stress).

## 3. Return Target
`p0935[T+1] / p1600[T] - 1` — pure overnight return on SQQQ. Entry at 16:00 close, exit at 09:35 next open.

## 4. Data Sources
- `minute_bars` table: VXX (signal), SQQQ (execution), SPY (benchmark)
- Checkpoints: p0935, p1530, p1600

## 5. Temporal Availability
- VXX 09:35 price: available at 09:35 ET
- VXX 15:30 price: available at 15:30 ET (spike detection)
- SQQQ 16:00 price: available at 16:00 ET (entry)
- SQQQ 09:35 next day: available at 09:35 ET T+1 (settlement)

## 6. Split Risk
VXX: frequent reverse splits (2023-03-07, 2024-07-24). SQQQ: reverse splits (2022-01-13, 2024-11-07). Both handled by magnitude filter (SPLIT_THRESHOLD=0.20). Note: 20% threshold may be aggressive for 3x ETFs but no trades censored in practice.

## 7. Expected Sharpe Range
Low (0.0 to 1.0). Spike events are rare (~12% of days). Signal is binary and concentrated. Academic literature on VIX-related strategies shows moderate Sharpe with high concentration.

## 8. Date Range
2022-01-18 to 2026-02-28 (per config.py). Train: through 2024-12-31. Test: 2025-01-01 to 2026-02-28.

## 9. Boundary Check
No calendar-period dependencies. Signal is event-driven (VXX spike), not periodic.
