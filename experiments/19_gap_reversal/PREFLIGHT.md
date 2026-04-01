# Pre-Flight: Experiment 19 — Gap Reversal

## Literature Grounding

WebSearch: "overnight gap reversal mean reversion stock cross-sectional academic paper out of sample"

- [Statistical Arbitrage with Mean-Reverting Overnight Price Gaps (S&P 500)](https://www.mdpi.com/1911-8074/12/2/51) — Jump-diffusion model, exploits overnight gaps in first minutes. Sharpe 2.38 after TC, 51.47% annual. S&P 500 constituents.
- [Overnight-Intraday Reversal Everywhere](https://assets.super.so/e46b77e7-ee08-445e-b43f-4ffd88ae0a0e/files/c953a0e6-e93e-4bf7-b839-45a90cedced4.pdf) — CO-OC reversal 5x larger than conventional short-term reversal. Cross-sectional dispersion predicts profitability. Liquidity provision is the driver.
- [Overnight returns, daytime reversals, and future stock returns](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21004116) — Overnight returns predict next-day reversals.
- [A Closer Look at Short-Term Return Reversal](https://www3.nd.edu/~zda/Reversal.pdf) — Reversal strongest in stocks with high overnight returns (both directions).

Key calibration: academic Sharpe 1.5–2.5 for gap reversal strategies with large universes. Our 153-symbol universe is smaller → expect lower Sharpe (0.5–1.5).

## 1. Hypothesis

Stocks with the largest negative overnight gaps (open << prev close) tend to mean-revert during the trading day. Buy the biggest gap-down stocks at 09:35, sell at 15:30. Cross-sectional: rank all 153 symbols by gap size, pick the N most negative.

## 2. Signal type

Price-based: overnight gap = `p0935[T] / p1530[T-1] - 1`. Cross-sectional ranking. **Mean-reversion** — opposite of momentum.

## 3. Return target

`p1530[T] / p0935[T] - 1` — intraday return from 09:35 entry to 15:30 exit, same day. The gap-down stock is expected to recover during the day.

## 4. Data sources

`minute_bars`: p0935, p1530 for 153 symbols + SPY, VXX. No FRED, no HMM — pure cross-sectional price signal.

## 5. Temporal availability

- p1530[T-1]: available T-1 15:30 — prev close for gap computation
- p0935[T]: available T 09:35 — open price, gap computed, entry
- p1530[T]: available T 15:30 — exit

## 6. Split risk assessment

Yes — split filter on gap computation (signal) and intraday return (settlement). Large gaps from splits would contaminate the signal.

## 7. Expected Sharpe range

0.5–1.5. Academic literature shows 1.5–2.5 on large universes (S&P 500). Our 153 symbols is smaller. > 2.0 triggers investigation.

## 8. Date range

2022-01-18 to 2026-02-28 (END_DATE).

## 9. Partial-period boundary check

No calendar-period signals. Cross-sectional ranking is daily. No risk.
