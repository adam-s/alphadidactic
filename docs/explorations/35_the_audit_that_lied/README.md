# The Audit That Lied

The complement strategy had a test Sharpe of 3.913. It was the best thing we'd built since the base overnight momentum signal. A GLD trend-following model — buy gold at the close when its EMA is positive, sell at the next open. Simple. Uncorrelated with the base. Positive in both the gold rally and the non-rally test periods. We built a whole honesty audit around it to make sure the numbers were real.

The audit had its own causal bug.

---

This is a story about what happens when the defense mechanism is the thing that's broken. The previous four explorations documented bugs in the data layer — timezone casts that shifted prices by five hours, `DISTINCT ON` queries that returned random rows, same-day return pairing, wall-clock model violations. Each one destroyed a pipeline. Each one taught a lesson that became a guardrail. By the time we started the complement research (experiments 84 through 109), we had CursorEngine enforcing per-day queries, pending_row preventing same-day pairing, and SPLIT_THRESHOLD catching split contamination. The base strategy — 47 experiments of iterative overnight momentum optimization — passed every check. Zero causal bugs across 47 directories. The architecture worked.

Then we tried to build something that trades during the other 78.6% of days.

---

The base overnight momentum strategy trades 21.4% of trading days. It goes flat during bear regimes (388 days), when VXX momentum is positive (154 days), on Mondays (121 days), and when no candidates pass the signal threshold. The complement research asked: what if we trade gold, silver, or inverse ETFs during those idle periods?

Twenty-five experiments later, we had promising results. GLD EMA trend-following showed a test Sharpe north of 2. Precious metals during bear regimes showed positive returns. An intraday morning-momentum strategy on SPY showed modest but genuine alpha during bear conditions. We built experiment 102 — the "honesty audit" — to validate the top strategies by decomposing their returns into gold-rally and non-rally periods, train and test, year by year.

The audit reported gld_ema20 at test Sharpe 3.913. Train Sharpe 3.35. Positive in every subperiod. It looked bulletproof.

We built the combined portfolio around it.

---

The bug was in the audit's simplification.

Experiment 99 — the actual GLD EMA strategy — uses the standard pending_row pattern. On day T, it observes the GLD overnight return, updates the EMA, checks if the EMA is positive, and enters GLD at the close. The return it earns is from day T's close to day T+1's open. Decision on T. Settlement on T+1. Six hours between the signal and the entry. Twelve hours between the entry and the exit. Nothing overlaps.

The audit (experiment 102) tried to reproduce this in a simplified loop. It cut a corner. Here's what it did:

```python
gld_on = overnight["GLD"]           # Today's overnight return (prev_close → open)
gld_ema.update(overnight["GLD"])     # Update EMA with today's return
gld_ema_val = gld_ema.get()          # Read the updated EMA

if gld_ema_val > 0:
    s2_ret = gld_on - 2 * TC         # Credit today's overnight return
```

Three lines. Signal and return on the same day.

Today's GLD overnight return pushes the EMA positive. The EMA being positive triggers the "trade." The return credited to the trade is the same overnight return that just pushed the EMA positive. The signal is correlated with the return because the signal *is* the return. This is exploration 33 — the return that already happened — wearing a different hat.

In the actual strategy (experiment 99), the EMA is updated on day T, the decision is made on day T, and the return comes on day T+1. Today's overnight return updates the EMA, but tomorrow's overnight return is the payoff. The correlation between "EMA is positive today" and "GLD goes up tomorrow overnight" is the genuine signal. It's about 0.06. The correlation between "EMA is positive today" and "GLD went up this morning" — which is what the audit measured — is about 0.21. The audit was measuring a correlation three times stronger than the real one.

Test Sharpe 3.913 in the audit. Test Sharpe 2.057 in the actual strategy.

---

We caught this during a full codebase audit. Not because we suspected the audit specifically, but because we decided to read every file in order.

121 Python files. Shared infrastructure, baseline, 109 experiments. The same twelve-point checklist on every one: timezone casts, DISTINCT ON patterns, same-day pairing, percentile gate ordering, accumulator timing, VXX momentum timing, win rate formulas, bulk price pre-loads, mixed holding periods. Start at `shared/`, end at `109_`.

The results were better than expected and worse than expected at the same time.

The core momentum pipeline — experiments 36 through 82 — was spotless. All 47 experiments passed every check. The CursorEngine + pending_row architecture that emerged from explorations 31-34 had done its job. The assertion `if entry_date >= today: raise AssertionError("Return pairing violation")` was in every file. The percentile gate appended after the threshold check in every file. VXX momentum was computed from close-to-close returns used in close-time decisions — temporally valid for overnight strategies. The early experiments (01-35) had a handful of minor issues — a couple of bulk price pre-loads, some hardcoded threshold values, a regime boundary using `<=` instead of `<` — but nothing that inflated returns.

The complement experiments told a different story.

---

Twenty of 26 complement files had a win rate calculation that always reported 100%:

```python
wr = float(np.mean([1 for r in wr_t if r > 0]) * 100)
```

This creates a list of 1's — one for each positive return — and averages them. The average of a list of all 1's is 1.0. Always. The formula was supposed to compute the proportion of positive trades, but it filtered out the negative trades before averaging, then averaged the survivors. Every experiment that used this formula reported 100% win rate regardless of actual performance.

The fix was trivial:

```python
wr = float(np.mean([r > 0 for r in wr_t]) * 100)
```

Booleans instead of filtered integers. After fixing all 21 instances and rerunning every complement experiment, the true win rates appeared: 40-58%. The returns and Sharpe ratios didn't change — this was a display bug, not a return-inflating bug. But it meant we'd been looking at "100% win rate" across 20 experiments without questioning it. When every strategy reports 100% wins, you stop using win rate as a diagnostic. The metric becomes invisible. A metric that's always the same value is the same as no metric at all.

---

The more interesting finding was about what *didn't* break.

We expected the VXX momentum timing to be a widespread bug. In the complement experiments, VXX daily returns are appended to a deque, and VXX momentum is computed from that deque. If the append happens before the decision, today's VXX close leaks into the momentum used to gate trades. Seventeen experiments had this pattern.

But here's the thing: all seventeen were overnight strategies. The decision happens at 16:00. VXX's close is at 16:00. Today's VXX close is observable at decision time.

For overnight strategies, this isn't look-ahead. It's using data that exists when you need it. The VXX close at 4:00 PM is a fact you can observe at 4:00 PM before deciding to enter a position at 4:00 PM. The concern was valid for intraday strategies — if you're deciding at 9:35 AM whether to enter a trade, you can't know VXX's 4:00 PM close. But only two experiments had intraday VXX gating, and only six configs within those experiments were affected.

We spent hours preparing to fix a bug in 17 experiments. The fix was needed in 6 configs across 2 experiments.

---

The accumulator timing told a similar story. The Accumulator class tracks per-symbol hit rates, average positive returns, and winning streaks. It gets updated with overnight returns (yesterday's close to today's open) at the top of each day's loop, before the signal computation later in the loop. The 96-109 audit agent flagged this as critical in experiment 107 — "today's return leaks into the accumulator state before the signal is computed."

But the 36-55 audit agent had examined the identical pattern in the base strategy and ruled it clean. The overnight return settles at 9:35 AM. The decision happens at 4:00 PM. Updating the accumulator with a return that settled six hours ago isn't look-ahead. It's a trader checking the morning tape before making an afternoon decision. The accumulator reflects "how reliable was this symbol overnight, including this morning?" — knowable at 9:35, confirmed at 16:00.

Two agents. Same code pattern. Opposite conclusions. The difference was context: one agent understood that overnight returns are observable before close-time decisions. The other didn't model the wall-clock timeline before flagging.

This is why the wall-clock diagram from exploration 34 matters. Without it, every data access that happens "before the decision in the code" looks suspicious. With it, you can distinguish between code ordering (which is arbitrary) and temporal ordering (which is physics). A line of code that runs at iteration step 3 and uses data from 9:35 AM, before a decision at step 7 that executes at 4:00 PM, is not look-ahead. It's just how loops work.

---

The complement experiments also surfaced a genuinely new bug class that the base strategy's architecture couldn't have caught: what happens when you have both overnight and intraday trades in the same loop.

The base strategy is overnight-only. One decision point (4:00 PM), one entry time (close), one exit time (next open). The loop has one phase. Everything is observable at the one decision time. Simple.

The complement strategies needed to trade intraday during bear regimes (buy GLD at the open, sell at the close) AND overnight during VXX-positive periods (buy GLD at the close, sell at the next open). Two holding periods. Two decision times. One loop.

The first version of experiment 109 made all decisions at the same point in the loop. This meant the intraday decision — which should happen at 9:35 — could see the close price. Not because anyone intended it, but because the close price was loaded earlier in the iteration and was sitting there in memory when the intraday decision code ran. The gap type `no_candidates` — which determines whether the base strategy has no good trades today — requires computing full intraday returns for 153 symbols. That's only knowable at 4:00 PM. Using it to gate a 9:35 entry is six hours of look-ahead.

The fix was structural: a two-phase loop. Phase 1 runs at 9:35 with only morning-observable data — regime (past), yesterday's VXX momentum, overnight returns. Phase 2 runs at 16:00 with the full day's data. Intraday decisions in Phase 1. Overnight decisions in Phase 2. The VXX momentum buffer is split into `vxx_rets_yesterday` (for Phase 1) and includes today's return only in Phase 2. The intraday accumulator is updated at end-of-day, after all decisions.

This is a bug that pending_row can't catch. Pending_row prevents same-day return pairing across days. It doesn't prevent same-day data leaking across decision phases within a day. The two-phase loop is a new guardrail for a new class of strategies.

---

Here's the final tally. 121 files audited. 96 clean. The 25 with issues broke down like this: 21 had the win rate display bug (fixed, returns unchanged). One had the audit's A3 bug (inflating Sharpe from 2.057 to 3.913). One had stale EMA updates on data-gap days. Two early experiments had bulk price pre-loads. A handful had minor threshold boundary choices.

Nothing in the core pipeline was wrong. The numbers we'd been building on — Test Sharpe 2.5 to 5.9 across 47 experiments, validated out-of-sample on 324 unseen R1000 symbols — survived intact. The complement strategies that used clean overnight-only architecture (experiments 99, 107, 88, 92) also survived. GLD EMA trend-following at Sharpe 2.057 is real. Bear-regime precious metals momentum at Sharpe 1.05 is real. They're more modest than the honesty audit claimed, but they're honest.

The audit that was supposed to verify them was the thing that needed verifying.

---

There's a pattern here that goes beyond backtesting. When you build a validation layer, that layer inherits the same risks as the thing it validates. The honesty audit cut a corner — it used a simplified return computation instead of the full pending_row machinery — and that simplification introduced exactly the bug the audit was designed to detect. The audit passed its own test because it was grading its own homework.

The defense against this is boring and slow: read every line of code in order, draw the wall-clock timeline, and check each data access against it. Not just the strategies. The audits. The analysis scripts. The helper functions. The "simplified version for quick checking." Especially the simplified version. Simplified code that skips causality guards is not a shortcut — it's a different program that answers a different question.

We have a rule now: any code that computes returns must use the pending_row pattern. Not "should." Must. Even in one-off analysis scripts. Even in honesty audits. If the code pairs a signal with a return, the pairing must cross a day boundary. No exceptions, no simplifications, no "this is just for quick validation."

The 3.913 Sharpe was the most dangerous number in the whole research pipeline. Not because it was the most wrong — the original timezone bug turned millions into -83%. It was dangerous because it came from the place we trusted most.

---

## Postscript: The Accounting Bugs (2026-03-15)

Five months after the original audit, a second class of bugs emerged. These weren't temporal — the CursorEngine and PhasedDay machinery caught every look-ahead violation. Instead, they were in the equity math itself.

**The position overlap bug (C2)** was the worst. When the base overnight strategy (5 stocks at 100% capital) and gold overnight (GLD at 100% capital) both entered on the same night, the backtest deployed 200% of capital — implicit leverage in a system explicitly designed to be debit-only. This happened on 168 of 1039 trading nights (16%). The fix — splitting capital 50/50 on overlap nights — dropped returns from +3,883% to +2,754%.

**The return-equity divergence (C5)** was subtler. The daily return was computed as an additive sum (`settle_ret + gold_id_ret`), but equity compounded multiplicatively through separate `equity *= (1 + x)` calls. The cross-term was silently lost. The Sharpe ratio computed from daily returns didn't match the actual equity curve. The fix: compute `day_ret = equity_after / equity_before - 1`.

**The SQQQ overlap (C8)** was discovered during the paranoid re-audit. After fixing C2, the auditor checked whether SQQQ + gold overnight could also overlap. They could — on VXX spike days with positive VXX momentum. Same fix: include SQQQ in the capital allocation logic.

The accounting bugs taught a lesson that the temporal bugs hadn't: **temporal correctness is necessary but not sufficient.** A strategy can use only past data, settle positions correctly, enforce PhasedDay monotonicity — and still report fake returns because the equity math assumes infinite capital. The accounting audit checklist (C1-C10) is now part of every production review alongside the temporal checklist (A1-R2).

The honest number, after all temporal AND accounting fixes: **+2,754%** on the development universe with default parameters. Still strong. Still real. But 30% lower than what the temporally-clean-but-accounting-bugged version reported.
