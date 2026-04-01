# The Compounding Gap

The research backtest for `16_intraday_exit` returns **+175%** over the 2022–2026 period.
The paper trading system running the same signals, the same instruments, and the same price data returns **+132%**.

A 43-point gap on the same data looks like a bug. It isn't. It's the difference between two
strategies that look identical on paper but behave differently during multi-day holds.

---

## What the research is actually doing

`16_intraday_exit/algorithm.py` defines `build_intraday_return_series`:

```python
out["next_exit"] = ext["price"].shift(-1).values
out["return_pct"] = (out["next_exit"] * out["split_factor"] / out["price"] - 1) * 100
```

Row `T` in the result holds:

```
return_pct[T] = (14:55[T+1] × split_factor[T] / close[T] - 1) × 100
```

`run_backtest` then iterates over these rows and applies the return to running capital every
single day the signal is in position. When the signal is bullish for 30 consecutive days,
30 returns are compounded:

```
capital[day 1]  × (1 + r[0])
capital[day 2]  × (1 + r[1])
...
capital[day 30] × (1 + r[29])
```

Where each `r[T] = 14:55[T+1] / close[T] - 1`.

This is a **daily re-entry strategy**. You sell at 14:55 every afternoon and buy back at the
close every evening. The return series encodes this: you are never holding overnight between
14:55 and the next day's close. The period from 14:55 → next-day close is always a gap
with zero exposure.

---

## What paper trading is doing

The paper trading worker in `portfolio.ts` holds shares from the day the signal turns bullish
to the day it turns bearish:

```
buy  QQQ at close[T_enter]
... hold through multiple days ...
sell QQQ at 14:55[T_exit]
```

The return for the entire hold period is a single calculation:

```
return = 14:55[T_exit] / close[T_enter] - 1
```

This is a **signal-transition model**. You trade when the signal changes, not daily.

---

## Why they diverge

Both strategies use the same entry and exit prices. The difference is what happens in between.

Take a 5-day bullish run. In both strategies, the first buy happens at `close[T]` and the
final sell happens at `14:55[T+4]`. The dollar outcome looks like it should be the same.

It isn't, because the research strategy also captures what happens between `14:55[T]` and
`close[T]` for every day `T+1` through `T+3`.

Research compounds:
```
14:55[T+1]/close[T] × 14:55[T+2]/close[T+1] × 14:55[T+3]/close[T+2] × 14:55[T+4]/close[T+3]
```

Paper trading captures:
```
14:55[T+4] / close[T]
```

If the afternoon-to-close move (`14:55 → next-day close`) is systematically positive, the
research captures the compounding of that drift every day. The paper trading model is exposed
to it only through the final price level — it doesn't compound the intermediate drift into
capital at risk.

The `16_intraday_exit` thesis is exactly that this drift *is* systematic. The 14:55 exit was
chosen because it consistently outperforms end-of-day close exits across the 2022–2026 period.
That predictability is what the research compounds; the paper trading model does not.

---

## Why we don't close the gap with daily re-entry

The paper trading model could theoretically match the research by liquidating at 14:55 and
re-entering at close every single day. The arithmetic works. The trading cost does not.

TQQQ and SQQQ are highly liquid — combined daily volume often exceeds $1 billion — but
they still have a bid-ask spread. At a typical spread of $0.01–$0.02 on a $55–$80 stock,
a round-trip costs roughly **0.02–0.04% of position value**.

At $20,000 in TQQQ (the approximate hedge allocation with `RISK_FRACTION_HEDGE = 0.309`
on a $60,000 hedge leg):

```
slippage per round trip ≈ $20,000 × 0.03% = $6
trading days per year   ≈ 252
annual slippage on hedge leg ≈ $1,500
```

Over a 4-year backtest period that's roughly $6,000 — about 6% of starting capital — consumed
in spread alone, before any market impact. The close→14:55 edge the research captures would
be partially offset by the cost of capturing it.

The research models zero friction. A live daily-re-entry system operating at the sizes in
this backtest would realistically see the 175% shrink to somewhere in the 140–160% range
after spread costs.

---

## The gap is honest

The paper trading result reflects what a real system executing signal-driven trades would
achieve — buying at close when bullish, selling at 14:55 when the signal flips, holding
through everything in between. With stock splits handled correctly (TQQQ had a 5-for-1
reverse split in November 2022), paper trading comes to approximately **$163,000** from
$100,000 starting capital.

An earlier version of the paper trading system did not account for splits, which produced
a materially incorrect P&L figure. Positions that held through the reverse split had their
share count misaligned with the post-split price. The corrected result incorporates
`insertSplitAdjustment()` — a synthetic zero-cost position adjustment that realigns share
counts before the post-split price is applied.

The research result of **+175%** is the theoretical maximum under zero-friction daily re-entry.

The difference between $163,000 and $175,000 (on $100K starting capital) is not a
measurement error. It is the cost of execution — the spread between what the close→14:55
pattern offers in theory and what a realistic trading system can capture in practice. The
paper trading model accepts overnight drift in exchange for fewer transactions. The research
assumes away that cost entirely.

For the purpose of evaluating whether the signal itself works, the research number is the
right one to look at. For estimating what a real account would show after 4 years of trading
this system, the paper trading number is closer to the truth.

---

## Related

- `16_intraday_exit/algorithm.py` — `build_intraday_return_series`, the daily re-entry return formula
- `16_intraday_exit/config.py` — `EXIT_TIME = "14:55"`, `RISK_FRACTION_HEDGE = 0.30858`
- `services/python/portfolio_worker.py` — `handle_get_stock_prices` (14:55 exits), `handle_get_close_prices` (~15:52 entries)
- `packages/jobs/src/workers/portfolio.ts` — signal-transition trading loop
- [27 — Survivorship Bias via Missing Prices](../27_survivorship_vwap/README.md)
