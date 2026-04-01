# Pre-Flight: Experiment 20 — Inverse ETF Split Signal

## Literature Grounding

WebSearch: "inverse ETF reverse split predicts market volatility shock corporate action signal"

- [Market Volatility Underscores Role of Inverse ETFs](https://www.etf.com/sections/news/market-volatility-inverse-etfs-sh-sqqq) — Inverse ETFs surge during volatility. Demand spikes → price erosion → reverse splits.
- [Volatility Shares Reverse Split announcement](https://www.sec.gov/Archives/edgar/data/1793497/000121390024113725/ea022634501ex99-1_vstrust.htm) — UVIX 1:10 reverse split Jan 2025. SEC filing is public signal.
- No academic paper found on reverse splits as predictive signals. **This is a novel hypothesis.**

## 1. Hypothesis

When inverse/leveraged ETFs undergo reverse splits, the ETF providers (with sophisticated quant teams) are signaling they anticipate future demand — i.e., upcoming market stress. The split is a public corporate action that precedes volatility shocks.

**Mechanism:** Inverse ETFs decay to low share prices during calm markets. Providers reverse-split to keep prices tradeable. But the TIMING of the split reflects the provider's internal view of upcoming volatility.

## 2. Signal type

Event-driven: corporate action (reverse split). NOT price-based. Binary signal with ~15 events in 2022-2026.

## 3. Return target

Multiple holding periods: SPY return over 1, 5, 10, 20, 40 trading days after each split event. Also VXX return (direct vol bet).

## 4. Data sources

- `minute_bars`: price discontinuities detect splits (|return| > 200%)
- Known splits from shared/config.py + manual identification from price data
- SPY and VXX prices for return measurement

## 5. Temporal availability

Split is observable at market open on the split date (price jumps 3x+). Signal is the OCCURRENCE of the split — public information.

## 6. Split risk assessment

The signal IS the split. Returns measured on non-splitting instruments (SPY, VXX). No split contamination risk on the return side.

## 7. Expected Sharpe range

N/A — event study with ~15 events. Cannot compute meaningful annualized Sharpe. Instead: measure average return, hit rate, and significance via permutation test.

## 8. Date range

2022-01-18 to 2026-02-28 (END_DATE).

## 9. Partial-period boundary check

N/A — event-driven, not calendar-based.
