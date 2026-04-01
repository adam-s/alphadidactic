# Five Hours Into the Future

The options flow signal looked real. The pipeline was careful — one day at a time against 1.5 billion rows of `options_trades`, split-adjusted returns, permutation-based p-values, train/test split at December 31, 2024. Twenty scripts built a research stack from statistical foundation through XGBoost ensemble to production reference. The KZ signal on SPY and QQQ had a Sharpe of +2.6 in-sample and the backtest turned $100,000 into millions.

The bug was in a four-character cast.

---

`minute_bars.time` is defined as `timestamp without time zone`. Polygon sends UTC timestamps and I stored them as-is. The column stores `2024-06-03 19:52:00` for a bar that closed at 3:52 PM Eastern on June 3rd. This is fine as long as you remember it's UTC.

I forgot.

Every price query in the research pipeline filtered trading hours like this:

```sql
AND time::time >= '09:30:00'
AND time::time <= '15:52:00'
```

`time::time` extracts the time-of-day component from the stored value. The stored value is UTC. So `time::time >= '09:30:00'` matches bars where the UTC clock reads 9:30 AM. In Eastern Time, that's 5:30 AM — an hour before premarket even opens on most platforms. And `time::time <= '15:52:00'` matches bars where UTC reads 3:52 PM, which is 11:52 AM Eastern.

The "close price at 3:52 PM" was actually the 11:52 AM price. The "open price at 9:30 AM" was the 5:30 AM pre-market price. Every overnight return in the pipeline was computed between the wrong prices.

---

Pick a specific day and trace it. SPY on June 3, 2024.

The buggy query — `time::time <= '15:52:00' ORDER BY time DESC LIMIT 1` — returns:

```
time: 2024-06-03 15:52:00    UTC time: 15:52    ET time: 11:52    close: $526.24
```

The correct query — with `((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time` — returns:

```
time: 2024-06-03 19:52:00    UTC time: 19:52    ET time: 15:52    close: $526.245
```

On this particular day the prices are nearly identical ($526.24 vs $526.245). That's a coincidence. The bug isn't that the price is always wrong by a large amount — it's that the price is from a different time of day, and the difference varies unpredictably. Some days the market moves a lot between noon and 4 PM. Some days it doesn't. The error is stochastic, which makes it invisible to any check that only looks at a few rows.

The open side was worse. The buggy query matched `time::time >= '09:30:00'`, which returned the 5:30 AM pre-market bar:

```
time: 2024-06-03 09:30:00    UTC time: 09:30    ET time: 05:30    close: $528.22
```

The correct 9:30 AM Eastern open:

```
time: 2024-06-03 13:30:00    UTC time: 13:30    ET time: 09:30    open: $529.02
```

The "overnight return" in the buggy pipeline was the move from an 11:52 AM close to a 5:30 AM pre-market open. Four hours of real trading were excluded from the close. The open was sampled before anyone was actually trading. The return between those two prices has almost nothing to do with the overnight move the strategy was supposed to capture.

---

This is look-ahead bias, but it's not the kind where a line of code reads `price[T+1]` instead of `price[T]`.

The flow signal was built from 3:00-4:00 PM Eastern options trades — that part of the pipeline was correct, because `sip_timestamp` is a proper `timestamptz` and the query used `AT TIME ZONE 'America/New_York'` directly on it. So the signal was genuinely from the last hour of trading. But the "close price" it was being compared against was from 11:52 AM. The signal at 4:00 PM was being evaluated against a price from four hours earlier.

When someone buys a lot of calls at 3:30 PM and the stock has already gone up between noon and 3:30 PM, it looks like the call buying predicted the move. It didn't. The move already happened. The signal was correlated with a return that had already been realized before the signal was observed. That's look-ahead bias — the effect appears before the cause in the data, even though no single line of code explicitly references a future timestamp.

The pipeline had twenty scripts and thousands of lines of price-fetching SQL. Every one of them used the same `time::time` cast. The bug was systematic, which paradoxically made it harder to catch. If only one query had been wrong, the returns would have looked inconsistent. Because every query was wrong in the same way, the returns were internally consistent — they just weren't measuring what they claimed to measure.

---

The fix was mechanical. Replace every `time::time` comparison with:

```sql
((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
```

The double conversion is necessary because of how Postgres handles `timestamp without time zone`. The first `AT TIME ZONE 'UTC'` tells Postgres "this value is in UTC" and converts it to `timestamptz`. The second `AT TIME ZONE 'America/New_York'` converts the `timestamptz` to a naive timestamp in Eastern Time. Then `::time` extracts the time-of-day. Without the first step, Postgres assumes the value is already in the session timezone (which on my machine is US/Eastern, so it would double-convert and produce the wrong answer in a different way).

Twenty scripts. Over forty individual SQL queries. About three hours to apply the fix and rerun everything.

---

The results divided cleanly into two categories.

The KZ-based signal — the statistical kernel that formed the backbone of the pipeline — collapsed. The production reference script (`10a_b1_production_reference`) went from positive returns to $100,000 becoming $16,659. That's -83.3%. The ATM-filtered variant (`12a`) went to $66,147. The share-based implementation (`13a`) matched at $66,147. Three independent implementations of the same signal, all confirming the same thing: the KZ signal doesn't work when you use the correct prices.

The XGBoost model survived. `10b_xgboost_production_reference` turned $100,000 into $452,000 with correct timezone handling. The ATM-filtered XGBoost variant (`12b`) reached $4.2 million. The ML model was apparently learning something beyond the timezone artifact — maybe the shape of the flow distribution, or a nonlinear interaction between features that the linear kernel missed. Or maybe the XGBoost model had its own bugs that I hadn't found yet. Survival is not the same as validity.

Here's the full table, every script rerun with dollar-based P&L ($100,000 initial capital, no daily rebalancing):

| Script | Strategy | Final Value | Return |
|--------|----------|------------|--------|
| 10a | B1 Production (KZ) | $16,659 | -83.3% |
| 12a | ATM-filtered KZ | $66,147 | -33.9% |
| 13a | Share-based KZ | $66,147 | -33.9% |
| 10b | XGBoost Production | $452,371 | +352.5% |
| 12b | ATM-filtered XGBoost | $4,231,103 | +4,131.1% |
| 11c | Config Backtest (baseline) | $39,932 | -60.1% |
| 11c | Config Backtest (atm20) | $97,322 | -2.7% |

The config backtest (`11c`) ran ten different signal configurations. Only one — ATM 20 strikes, the narrowest filter — came close to breaking even. The rest lost money. The baseline lost 60%.

---

The pipeline had tests. It had parity checks. The KZ incremental-vs-batch verification passed to 14 decimal places, before and after the fix. The split adjustment logic was correct. The flow extraction timestamps were correct. The cooldown implementation was correct. None of those tests caught the timezone bug because none of them tested the boundary between the flow data (which was in `timestamptz` and handled correctly) and the price data (which was in `timestamp without time zone` and handled incorrectly). The two data sources entered the pipeline through different SQL queries with different column types, and the bug lived in the gap between them.

I spent weeks building infrastructure to prevent look-ahead bias: expanding-window kernels, delayed cooldown evaluation, per-date bucket existence checks. The actual look-ahead bias was a Postgres type cast that shifted every price by four hours.

The thing about `timestamp without time zone` is that it works perfectly until you forget what timezone it's in. And the thing about forgetting is that the code still runs. You get results. The results look reasonable. The Sharpe ratio is good. The equity curve goes up. You keep building on it. Twenty scripts later, someone asks "what time does 15:52 UTC correspond to in New York?" and the whole thing unravels in an afternoon.
