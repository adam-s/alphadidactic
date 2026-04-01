# Pre-Flight: Experiment 17 — Base Rejects

## Literature Grounding

WebSearch: "near-miss momentum stocks second tier signal percentile gate overnight alpha academic paper"

- [Alpha from Short-Term Signals](https://alphaarchitect.com/alpha-from-short-term-signals/) — Momentum and reversal coexist short-term. Percentile-based gating (15%, 10%, 5%) with break-even TC analysis.
- [Putting an Academic Factor into Practice: Momentum](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Putting-and-Academic-Factor-Into-Practice.pdf) — AQR on how momentum translates to practice with multi-signal gating.
- [Momentum: 30 years after Jegadeesh and Titman](https://link.springer.com/article/10.1007/s11408-022-00417-8) — Survey of momentum including discussion of second-tier signal capture.

## 1. Hypothesis

When the primary overnight signal fails the 74th percentile gate, the near-miss stocks still have positive momentum + high hit rate. Entering them with a lower gate captures residual alpha on idle bull-regime nights.

## 2. Signal type

Price-based: cross-sectional overnight momentum (accumulator hit rate + streak). Same signal as primary, different gate threshold.

## 3. Return target

`p0935[T+1] / p1530[T] - 1` — overnight return, entry at 15:30, exit at 09:35 next day.

## 4. Data sources

`minute_bars`: p0935, p1530, p1600 for 153 symbols + SPY, VXX. `fred_releases` for MacroRegime.

## 5. Temporal availability

p0935 at 09:35 (settle), p1530 at 15:30 (entry + signal), FRED < today.

## 6. Split risk assessment

Yes — split filter on accumulator and settlement.

## 7. Expected Sharpe range

1.0–2.0. Rejects are weaker signals by definition. Combined may improve by filling idle nights.

## 8. Date range

2022-01-18 to 2026-02-28 (END_DATE). Reference used date.today() — converted.

## 9. Partial-period boundary check

No calendar-period signals. Rolling percentile history. No risk.
