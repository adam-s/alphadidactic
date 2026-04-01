# Pre-Flight: Experiment 15 — Cross-Sectional Gold Ranking

## Literature Grounding

WebSearch: "cross-sectional precious metals momentum EMA ranking gold silver intraday academic paper"

- [Economic drivers of volatility and correlation in precious metal markets](https://www.sciencedirect.com/science/article/pii/S240585132100074X) — Mixed data sampling for precious metal dynamics. Confirms co-movement across gold/silver/platinum with regime-dependent correlation.
- [Short-term and long-term relationships between gold and precious metals](https://www.tandfonline.com/doi/full/10.1080/1331677X.2017.1305778) — Gold leads silver/platinum in short-term momentum. Cross-sectional ranking has theoretical basis.

Limited academic literature on intraday cross-sectional precious metal momentum specifically. The hypothesis is practitioner-motivated: when one precious metal shows stronger trend, rotate into it.

---

## 1. Hypothesis

Dynamically selecting the strongest-trending precious metal (by EMA of overnight returns) outperforms fixed-instrument trading (always NUGT). Cross-sectional momentum within the precious metals complex.

## 2. Signal type

Price-based: OnlineEMA of overnight returns per instrument.

## 3. Return target

`p1600[T] / p1030[T] - 1` — intraday return from 10:30 entry to 16:00 exit, same day. Equal-weight if top-2.

## 4. Data sources

- `minute_bars` table: p0935, p1030, p1600 for GLD, GDX, NUGT, SLV, SIL, SPY, VXX
- No FRED/HMM, no flow data

## 5. Temporal availability

- p0935: available 09:35 ET — used for EMA update (overnight return = p0935/prev_p1600)
- p1030: available 10:30 ET — entry price
- p1600: available 16:00 ET — exit price, stored as prev_close for next day's EMA

## 6. Split risk assessment

Yes — split filter on both EMA update (overnight return) and intraday return settlement.

## 7. Expected Sharpe range

0.5–1.5. Gold intraday momentum is modest. Cross-sectional selection may improve by ~0.1–0.3 Sharpe over fixed instrument.

## 8. Date range

2022-01-18 to 2026-02-28 (END_DATE). Reference used date.today() — converted.

## 9. Partial-period boundary check

No calendar-period signals. EMA is rolling. No partial-period risk.
