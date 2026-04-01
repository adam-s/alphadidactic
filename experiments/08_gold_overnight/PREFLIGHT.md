# Pre-Flight: 08 Gold Overnight

## 1. Hypothesis
GLD overnight returns are predictable using an EMA trend filter, gated by macro regime (bull only) and VXX momentum (positive = risk environment favoring gold hedging).

## 2. Signal Type
Price-based trend (EMA-16 of GLD overnight returns) + macro regime (HMM on FRED panel) + volatility momentum (VXX 20-day).

## 3. Return Target
`p0935[T+1] / p1600[T] - 1` — GLD overnight return. Entry at 16:00 close, exit at 09:35 next open.

## 4. Data Sources
- `minute_bars`: GLD (signal + execution), VXX (momentum gate), SPY (benchmark)
- `msvar_panel.parquet`: FRED macro features for HMM regime classification

## 5. Temporal Availability
- GLD p0935: available 09:35 ET (EMA update with overnight return)
- VXX p1600: available 16:00 ET (momentum including today's close)
- GLD p1600: available 16:00 ET (entry price)
- FRED data: publication lag respected by MacroRegime (strict `< today`)

## 6. Split Risk
GLD: no historical splits. VXX: frequent reverse splits (handled by magnitude filter).

## 7. Expected Sharpe Range
Moderate (0.5 to 1.5). Gold overnight momentum is documented in commodity literature.

## 8. Date Range
2022-01-18 to 2026-02-28. Train through 2024-12-31.

## 9. Boundary Check
Monday logic: strategy enters on Mondays (weekend gap). No partial-period issues.
