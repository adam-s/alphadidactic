# Pre-Flight: Experiment 16 — Adaptive Exit Timing

## Literature Grounding

WebSearch: "adaptive exit timing overnight gap take profit momentum strategy academic paper"

- [Momentum investing and intraday/overnight returns (Taiwan)](https://www.sciencedirect.com/science/article/abs/pii/S0927538X23002226) — Investors underreact to intraday info, overreact to overnight. Suggests profit-taking on large gaps is rational.
- [The Momentum Gap and Return Predictability](https://academic.oup.com/rfs/article-abstract/35/7/3303/6368076) — Gap size negatively predicts momentum profits. 1 std increase → 1.25% decrease in monthly return.
- [A tug of war: Overnight versus intraday expected returns](https://personal.lse.ac.uk/polk/research/TugOfWar.pdf) — Overnight and intraday returns have opposite characteristics.

## 1. Hypothesis

When overnight gap is large and positive, exit at 09:35 (take profit before mean-reversion). When gap is small/negative, hold to 10:30 (let it develop). Adaptive exit outperforms fixed timing.

## 2. Signal type

Price-based: cross-sectional overnight momentum (accumulator hit rate + streak).

## 3. Return target

- Early exit: `p0935[T] / p1530[T-1] - 1`
- Late exit: `p1030[T] / p1530[T-1] - 1`

## 4. Data sources

`minute_bars`: p0935, p1030, p1530, p1600 for 153 symbols + SPY, VXX. `fred_releases` for MacroRegime.

## 5. Temporal availability

p0935 at 09:35 (gap + early exit), p1030 at 10:30 (late exit), p1530 at 15:30 (entry), FRED < today.

## 6. Split risk assessment

Yes — split filter on accumulator and both exit paths.

## 7. Expected Sharpe range

1.0–2.5. Base overnight on training symbols is strong.

## 8. Date range

2022-01-18 to 2026-02-28 (END_DATE). Reference used date.today() — converted.

## 9. Partial-period boundary check

No calendar-period signals. Rolling gap history. No risk.
