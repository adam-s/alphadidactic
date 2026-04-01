# Pre-Flight: 09 Gold Intraday

## 1. Hypothesis
When GLD overnight returns trend positive (EMA-34 > 0), gold miners (NUGT, 3x leveraged) continue the momentum intraday.

## 2. Signal Type
Price-based trend (EMA-34 of GLD overnight returns applied to NUGT intraday).

## 3. Return Target
`p1600[T] / p1030[T] - 1` — NUGT intraday return. Entry at 10:30, exit at 16:00 same day.

## 4. Data Sources
- `minute_bars`: GLD, GDX, NUGT (signal + execution), SPY (benchmark)
- Checkpoints: p0935, p1030, p1600

## 5. Temporal Availability
- GLD/GDX/NUGT p0935: available 09:35 ET (EMA update)
- NUGT p1030: available 10:30 ET (entry, 55 min after signal)
- NUGT p1600: available 16:00 ET (exit)

## 6. Split Risk
NUGT: leveraged ETF, potential reverse splits. 20% threshold may be aggressive for 3x instrument but no trades censored in data range.

## 7. Expected Sharpe Range
Moderate (0.5 to 1.5). Leveraged ETF intraday momentum is well-documented.

## 8. Date Range
2022-01-18 to 2026-02-28. Train through 2024-12-31.

## 9. Boundary Check
No calendar-period dependencies. Intraday strategy, no overnight holding.
