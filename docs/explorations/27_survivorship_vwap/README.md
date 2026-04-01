# The Free Cancellation

The backtest had a Sharpe ratio of 4.03 and a 1,226% return. It traded daily bull call spreads on QQQ, entering and exiting at 15:30 ET using VWAP prices from a precomputed parquet cache. The signal came from SPY options flow — retail 0DTE minus institutional 3-month, z-scored and smoothed. Everything about the pipeline was careful. The signal was honest. The pricing was real VWAP from `options_trades`. The position sizing compounded. The train/test split was clean.

The bug was in what happened when there was no price.

---

The options VWAP cache works like this. For each trading day, query `options_trades` for all trades within a narrow window — 15:48 to 15:52 ET — grouped by strike and expiration. Compute the volume-weighted average price. Store the result. If a particular strike has no trades in that 4-minute window, fall back to a wider window (15:40 to 16:00 ET) for core strikes within a few dollars of spot.

This gives you a clean exit price for most positions. But "most" isn't "all." Some strikes have no trades anywhere near the close. These are the far OTM legs of wider spreads — strikes 8 or 9 dollars from spot on a $450 underlying. Nobody is trading a QQQ call at $459 when QQQ is at $450. The market maker's quote is a penny bid, and even that might not be there.

The backtest's `close_position_v2` function tried to exit by looking up both legs in the cache. If both legs had VWAP prices, it computed the credit and recorded the P&L. If the position was at expiration, it used intrinsic value — max(0, spot - strike) for calls. But if neither worked — no VWAP, not at expiration — the function returned `valid=False`.

Here's what the calling code did with that:

```python
else:
    capital += pos["cost"]
    counts["invalid_closes"] += 1
```

It refunded the premium. The cost of entering the spread was added back to capital as if the trade never happened. No P&L recorded. No trade in the ledger. The position just vanished.

---

This is look-ahead bias, but it doesn't look like look-ahead bias. There's no future price leaking into a past decision. No signal using tomorrow's returns. The bias is subtler: the backtest is using the *absence* of future information to retroactively cancel a trade.

Think about what "no VWAP at 15:30" actually means. It means nobody traded that option near the close. And nobody traded it because it was worthless — or close enough to worthless that no counterparty bothered. If you're holding the long leg of a call spread at a strike 9 dollars above spot, and the option expires in two days, that contract has almost no value. The market knows this. That's why there are no trades.

The backtest doesn't know this. It sees a missing price and treats it as a data quality problem — "we don't have a reliable exit price, so let's skip this one." But skipping it means pretending you weren't holding a losing position. You were. You bought a spread for $0.28 per contract, the short leg moved against you, and at 15:30 the next day your long leg is deep OTM with no buyers. In reality you're a bag holder with a loss. In the backtest you got a free cancellation.

---

The numbers told the story before the code did. Running the backtest with `ledger=True` and checking the trade log:

- 1,186 total positions opened
- 384 silently dropped (32.4%)
- 802 with recorded exits

Nearly a third of all positions were erased from the record. And they weren't random. The dropped positions were overwhelmingly losers — they were the ones where the short leg expired worthless or near-worthless, which happens exactly when the spread moved against you.

To confirm this wasn't a data problem, I queried `options_trades` directly for 15 of the dropped positions. Every single one had real trades in the database near 15:30 ET. Some were essentially worthless — a penny or two for deep OTM contracts. But some had real value: $18.72 for an ITM option that the cache simply didn't cover because it was outside the gap-fill range.

The cache had been built with a query range of +/-$10 from spot and a gap-fill core of +/-$5. A spread with width 10 puts the short leg at ATM+9 — outside the $5 gap-fill range. The data existed. The cache just didn't reach far enough.

---

Widening the cache was the first fix. Query range went from +/-$10 to +/-$20, gap-fill core from +/-$5 to +/-$15. The cache grew from ~400K rows to 1.58M rows (589K gap-filled). Build time: 8 minutes across 4 parallel workers.

With the wider cache, the drop rate fell from 32.4% to 4.7%. But the Sharpe fell too — from 4.03 to 0.68. Those weren't data quality problems being fixed. Those were losing trades being counted.

The second fix was in `_close_positions`. When `close_position_v2` returns `valid=False`, the position cost was already deducted at entry. The right thing to do is nothing — leave capital as-is and record a full loss of premium:

```python
else:
    # No VWAP and not at expiration — treat as full loss of premium.
    # The cost was already deducted at entry; do NOT refund it.
    counts["invalid_closes"] += 1
```

No refund. No free cancellation. If you can't exit, you lose what you paid.

---

After both fixes, Optuna found a new best configuration. Width 2, DTE 3, OTM offset 0. Train Sharpe: 1.76. Test Sharpe: -0.53.

The optimizer couldn't find anything that worked out of sample. With the free cancellation, width 10 spreads looked brilliant — the losers disappeared and only the winners remained. Without it, wider spreads are just more positions in illiquid strikes where you can't get out.

The width=1 grid winner survived better. Optuna tuned it to a train Sharpe of 2.59 and test Sharpe of 1.44 — but only with very conservative sizing (2.5% risk per leg instead of 10%). The signal has edge. The execution just can't be casual about it.

---

The pattern generalizes beyond options backtesting. Any time a backtest skips a trade because data is missing, ask why the data is missing. If the answer is "because the market for that instrument was dead," then skipping the trade is the same as erasing the loss. The missing data isn't noise — it's the signal that you lost.

Survivorship bias usually refers to excluding dead companies from a stock universe. This is the same mechanism applied to individual positions: the ones that "survived" into the cache are the ones with active markets, which are the ones with value. The ones that didn't survive are the worthless ones — your losses.

A missing price isn't a data quality issue. It's a price of zero that the market was too indifferent to print.
