# The Return That Already Happened

A simple moving average strategy on TSLA returned +7,764% in four years. The signal: if today's close is above the 20-day moving average, go long overnight. If below, go short. One feature, one stock, one threshold. The test Sharpe was 1.89. On NVDA, the MA20 long/short returned +6,755%. Six other stocks showed similar results.

The strategy is a textbook indicator applied to overnight returns. There's nothing novel in it. That's what made the numbers suspicious. An MA20 crossover shouldn't produce a Sharpe of 1.89 on anything, let alone on a single stock over a period that included 2022.

The bug took a day to find. It wasn't in the data, the timezone handling, the split filter, or the return computation. It was in the pairing of a decision to the return it earned.

---

The overnight return on day D is `p0935[D] / p1552[D-1] - 1`. You buy at yesterday's 15:52 close and sell at today's 9:35 open. This return settles at 9:35 AM on day D. By 15:52 on day D — when the strategy makes its next decision — the overnight return for day D is six hours old.

The code computed the MA20 signal at 15:52 on day D and applied it to the overnight return of day D. The return that the signal was supposedly predicting had already happened before the signal was computed. The decision came after the settlement.

The loop looked like this:

```python
for T in trading_dates:
    ovn_ret = p0935[T] / p1552[T-1] - 1   # settled at 09:35
    above_ma20 = p1552[T] > ma20[T]         # computed at 15:52
    if above_ma20:
        day_ret = ovn_ret - 2 * TC          # pairing decision[T] with ovn_ret[T]
```

The correct version carries the decision forward:

```python
prev_decision = None
for T in trading_dates:
    ovn_ret = p0935[T] / p1552[T-1] - 1
    if prev_decision is True:
        day_ret = ovn_ret - 2 * TC          # prev decision earns today's return
    prev_decision = p1552[T] > ma20[T]      # today's decision for tonight
```

The difference is one variable. The decision made at T 15:52 should earn the return from T 15:52 to T+1 9:35. Instead, it was earning the return from T-1 15:52 to T 9:35 — a return that had already been realized six hours before the decision was made.

---

The correlation explains why this produces fake alpha. The close at 15:52 is correlated with the overnight return that settled earlier that morning. Compute `corr(p1552[T], ovn_ret[T])` and you get +0.21. When the market opens up (positive `ovn_ret`), it tends to continue higher through the close (`p1552` is higher). So `p1552[T] > ma20[T]` is more likely to be true on days when `ovn_ret[T]` was positive. The signal appears to predict the return because it's computed from data that shares a common cause with the return — the same day's market direction.

With correct alignment — `corr(p1552[T-1], ovn_ret[T])` — the correlation drops to +0.07. Almost nothing. The MA20 crossover from yesterday's close has near-zero predictive power over tonight's overnight return. Which is what you'd expect from a simple moving average.

The corrected results:

| Stock | Buggy Return | Corrected Return | Test Sharpe |
|-------|-------------|-----------------|-------------|
| TSLA  | +7,764%     | +177%           | 0.11        |
| NVDA  | +6,755%     | +50%            | -0.25       |

Every stock collapsed. Test Sharpe went from 1.89 to 0.11 on TSLA. The T9 variant (a more complex signal combining MA20 with hit-rate and streak) collapsed identically. Six stocks, two signal variants, all the same pattern: dramatic results with same-day pairing, near zero with correct alignment.

---

The experiment had a TemporalGuard — a framework that wraps every data access and raises an exception if you read a value that hasn't been observed yet. The guard was configured correctly. It checked that `p1552[T]` was observable at 15:52 on day T (it is). It checked that `ovn_ret[T]` was observable at 15:52 on day T (it is — it settled at 9:35). Every individual data access passed. Zero violations.

The guard verifies "can I see this data at decision time?" It doesn't verify "is this the return my decision should earn?" The overnight return for day T is visible at 15:52 on day T. That's not the question. The question is whether the decision at 15:52 on day T should be paired with a return that settled six hours ago or with a return that will settle tomorrow morning. The guard can't answer that because the pairing isn't a data access — it's a logical relationship between two correct values.

This is a category of look-ahead bias that individual-access verification cannot detect. The data is all legitimately observable. The temporal ordering of observations is correct. The error is structural — the wrong return is attributed to the right decision. I added this as A13 in the bias checklist with a specific test: run the strategy both ways (same-day pairing and prev-day pairing) and compare. If same-day is dramatically better, the alpha is fake.

---

The next question was whether the mature experiments — the ones reporting +1,779% and +11,157% — had the same bug.

They don't. The older experiments use a different code pattern. Instead of computing decision and return on the same row, they store positions as state variables across loop iterations. The stock position is set at 15:52, persists overnight as `self.stock_position`, and is settled the next morning at 9:35 via a `_settle_stock()` method that uses the stored entry price. The settle/decide/store sequence enforces correct alignment by construction — you can't settle a position that doesn't exist yet, and you can't attribute a return to a decision that hasn't been made.

I traced the code line by line in five experiments:

| Experiment | Pattern | Lines | Status |
|-----------|---------|-------|--------|
| 08_grand_combination | `_settle_stock()` with stored entry_price | 367-377 | Clean |
| 36_guarded_hybrid | Pending mechanism, TemporalGuard at 09:36 and 15:53 | 386-453 | Clean |
| 11_combined | Same settle/decide/store as 08 | 243-261 | Clean |
| 13_structural_alpha | Multi-position variant, same structure | 193-214 | Clean |
| 23_mean_reversion | `next_p0935 = shift(-1)`, ret = next_p0935/p1552-1 | 26-27 | Clean |

The A13 bug was specific to experiment 61 — a newer, simpler script that used a single-pass loop without position state. The simplicity was the trap. The older experiments were correct because their architecture was more complex in a way that happened to prevent this exact error.

---

While auditing experiment 36, I found three separate issues in the VXX and SQQQ legs.

The strategy shorts VXX (a volatility ETN that decays due to contango) and occasionally holds SQQQ (a 3x inverse QQQ ETF). Both positions use close-to-close daily returns — enter at yesterday's close, exit at today's close. The return `close[D] / close[D-1] - 1` is not known until the market closes at 4:00 PM on day D.

The TemporalGuard was checking VXX settlement with `obs_time_et="09:35"`. That's the settlement time for the stock leg, not VXX. Today's VXX close is six and a half hours away at 9:35 AM. The guard passed because 9:35 is before the decision time of 15:53 — but the actual observation time is 16:00, not 9:35. The guard was verifying the right condition with the wrong inputs.

SQQQ had no guard check at all.

The `total_violations` counter was initialized to zero and never incremented.

I fixed all three: VXX guard checks now use `obs_time_et="16:00"` with a `guard.set_decision(today, "16:01")` context. SQQQ has its own guard check. The code separates overnight settlement (stock and QQQ shorts at 9:35) from close-to-close settlement (VXX and SQQQ at 16:00).

These fixes don't change the returns. VXX settlement is a capital event, not a decision input — no signal depends on VXX's intraday return. The portfolio uses fixed allocation weights, not dollar amounts, so the timing of settlement within the day doesn't affect position sizing. The bug was dishonest labeling, not return inflation. But dishonest labeling is how real bugs hide. A guard that claims zero violations when the observation times are wrong is worse than no guard at all — it creates false confidence that survives audit.

---

The final question was about leverage.

Experiment 36 Config D reported +11,157% with allocations of 100% to the stock leg and 70% to the VXX short. That's 170% gross exposure — 1.7x leverage with no margin cost, no borrow fee, no liquidation risk. The code doesn't model any of these.

I added a `stock_weight` parameter and reran everything with total allocation capped at 100%:

| Config | Allocation | Return | Sharpe | DD |
|--------|-----------|--------|--------|-----|
| G | Stock 100% | +2,242% | 2.064 | 26.4% |
| A | Stock 100% + QQQ short bear | +2,318% | 2.072 | 27.4% |
| F | Stock 60% + VXX 40% | +2,069% | 2.292 | 23.2% |
| D | Stock 30% + VXX 70% | +1,194% | 1.761 | 29.6% |

Previous Config D with 1.7x leverage: +11,157%. Without leverage: +1,194%. A 9.3x reduction.

But look at Config G — pure stock picking, no VXX, no SQQQ, no bear hedging. +2,242% at Sharpe 2.064. That's the real alpha. The B2 signal (rank 153 stocks by intraday momentum plus streak, buy the winner overnight) produces genuine risk-adjusted returns at 1x. The VXX short doesn't add alpha at 1x — it takes capital away from the higher-returning stock leg and replaces it with contango decay. Config F (60/40 stock/VXX) has the best Sharpe (2.292) because VXX reduces volatility, but it reduces returns too.

VXX contango is a real phenomenon — VXX has lost roughly 90% over four years. But at 1x allocation, it's a diversifier that improves Sharpe by 0.2 points while reducing total return by 200 percentage points. At 1.7x leverage, it's a return multiplier that compounds a real signal into +11,157%. The leverage doesn't create alpha. It amplifies alpha that already exists in the stock leg and makes it impossible to tell where the return is actually coming from.

The year-by-year breakdown shows the strategy degrades gracefully in the test period:

```
Config F (60/40 stock/VXX):
  2022:   +55.5%  Sharpe 1.56
  2023:  +172.5%  Sharpe 3.44
  2024:  +160.7%  Sharpe 2.62
  2025:   +92.8%  Sharpe 1.94
  2026:    +1.9%  Sharpe 0.53
```

2025 Sharpe 1.94 degrades from 2024's 2.62. That's not a collapse — it's a strategy that works less well in a different market. 2026 is two months of data and meaningless.

---

Two illusions in one audit. The first was a bug — a decision paired with a return that had already happened, inflating a simple MA20 strategy by 44x. The second was leverage — implicit 1.7x exposure with no friction costs, inflating +2,242% into +11,157%. The TemporalGuard caught neither, because neither was a data access violation. One was a pairing error. The other was an accounting assumption.

The strategy that survived both is the one that was never complicated. Pick the stock with the best intraday momentum and the best recent hit rate. Buy it at 15:52. Sell it at 9:35 the next morning. Do that every day for four years. +2,242% at Sharpe 2.06. No VXX. No SQQQ. No leverage. No regime gating required for the core signal, though it helps at the margins.

The 93% of returns that came from leverage weren't fake — they were real returns that a leveraged portfolio would have earned in a period with no VIX catastrophe. But a backtest that runs from 2022 to 2026 without a February 2018 or a March 2020 is a backtest that has never been tested on the thing that kills VXX shorts. The +11,157% number is technically correct. It's also the wrong number to plan around.

---

## Related

- [Exploration 31 — Five Hours Into the Future](../31_timezone_look_ahead/README.md) — the timezone cast bug
- [Exploration 32 — The Query That Looked Correct](../32_distinct_on_mirage/README.md) — the DISTINCT ON mirage
- Research: `atm_decomposition_fred/61f_correct_alignment/` (A13 proof)
- Research: `atm_decomposition_fred/36_guarded_hybrid/` (leverage audit)
