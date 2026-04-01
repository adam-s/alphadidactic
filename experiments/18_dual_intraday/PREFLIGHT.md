# Pre-Flight: Experiment 18 — Dual Intraday Checkpoint

## Literature Grounding

WebSearch: "intraday compounding split day morning afternoon trading legs academic paper"

- [Intraday Option Return: A Tale of Two Momentum](https://www3.nd.edu/~zda/IntraOption.pdf) — Morning and afternoon momentum are distinct. 10am and 4pm returns uncorrelated. Monthly alpha 9.3%.
- [Market intraday momentum](https://www.sciencedirect.com/science/article/abs/pii/S0304405X18301351) — Morning returns predict afternoon returns.
- [A tug of war: Overnight vs intraday](https://personal.lse.ac.uk/polk/research/TugOfWar.pdf) — Overnight and intraday returns have opposite characteristics.

## 1. Hypothesis

Splitting NUGT intraday (10:30→16:00) into two half-day legs doubles compounding cycles, improving returns if both halves are independently profitable.

## 2. Signal type

Price-based: OnlineEMA(34) of NUGT overnight returns.

## 3. Return target

- Single: `p1600[T] / p1030[T] - 1`
- Dual: `(1 + p_split/p1030 - 1) * (1 + p1600/p_split - 1) - 1`

## 4. Data sources

`minute_bars`: p0935, p1030, p1230/p1300/p1330/p1400, p1600 for NUGT, GLD, GDX, SPY, VXX.

## 5. Temporal availability

p0935 at 09:35 (EMA update + signal), all other checkpoints same-day.

## 6. Split risk assessment

Yes — split filter on EMA update and each half-day return.

## 7. Expected Sharpe range

0.5–1.5. Dual compounding adds return but also adds TC (4*TC vs 2*TC).

## 8. Date range

2022-01-18 to 2026-02-28 (END_DATE).

## 9. Partial-period boundary check

No calendar-period signals. No risk.
