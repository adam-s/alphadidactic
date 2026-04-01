# The Wall-Clock Model

The backtester returned +2366%. It ran day-by-day out of the real database, queried one trading day at a time, and carried a position from SELL to BUY the same way live code would. The look-ahead checklist came back clean. The timezone handling was correct. The train/test split was enforced. Everything said it was fine.

I still didn't trust it.

Not because I found a specific bug. But because "no per-day look-ahead" is a claim, and claims about backtesting need to be earned. The only way to earn it is to sit down and write out, for every line of code that touches data, exactly what the system could know at that moment — and why.

That document is what I started calling the wall-clock model.

---

The strategy trades overnight. Buy at 15:52 ET, sell at 09:35 ET the next morning. Every decision happens at one of two clock times. Nothing else matters. The question for every data access in the code is: *which side of those two timestamps does this fall on?*

I added a diagram to the module docstring — not for a reader, but for myself. A forcing function:

```text
┌─────────────────────────────────────────────────────────────────┐
│ 09:35 ET  (T)  "Opening window"                                 │
│                                                                 │
│  · DB query for p0935 window [09:30–09:35 ET] fires NOW.       │
│  · SETTLE: yesterday's position exits at p0935[T][sym].        │
│  · ACCUMULATOR UPDATE: overnight rets for the full universe.   │
├─────────────────────────────────────────────────────────────────┤
│ 15:52 ET  (T)  "Decision window"                                │
│                                                                 │
│  · DB query for p1552 window [15:47–15:52 ET] fires NOW.       │
│  · REGIME: GaussianHMM fitted on fred_panel.index < T.         │
│  · SIGNAL: iret, hit_rate, streak — all from before 15:52.     │
│  · PERCENTILE GATE: signal_history[0..T-1] only.               │
│  · EXECUTE: chosen symbol stored as pending.                   │
├─────────────────────────────────────────────────────────────────┤
│ T+1  09:35 ET  — SELL fires                                    │
└─────────────────────────────────────────────────────────────────┘
```

Six steps. Two timestamps. Drawing this made a problem visible almost immediately.

---

## The Query Bounds

The DB query fetched prices for a given `trade_date`. It didn't use a wildcard — it had explicit time windows, a narrow 5-minute band around each target. That's important. You can't accidentally fetch a bar from 3:55 PM when you're querying a window that closes at 3:52.

But the bounds were computed from date strings passed as UTC midnight, and the columns in `minute_bars` are stored as UTC. So the bound calculation had to be right, not just present. The code used `ZoneInfo("America/New_York")`:

```python
def et_to_utc(d: date, hour: int, minute: int) -> datetime:
    et_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)
    return et_dt.astimezone(UTC)
```

On March 10, 2024 (the last day of EST), 9:35 AM Eastern is 14:35 UTC. On March 11, 2024 (the first day of EDT), 9:35 AM Eastern is 13:35 UTC. If you hardcode `-5` or `-4` you get the wrong bound on a quarter of the trading days in March and November. Previous experiments in this series had been burned by this exact error — [Exploration 31](../31_timezone_look_ahead/README.md) is entirely about it.

I added a `DST_AUDIT_DATES` set — the days before and after each spring and fall transition — and logged the actual UTC bounds on those days:

```text
[TZ-AUDIT] 2024-03-10 — EST (UTC-5)
  p0935 window: [2024-03-10 14:30:00, 2024-03-10 14:35:00] UTC
[TZ-AUDIT] 2024-03-11 — EDT (UTC-4)
  p0935 window: [2024-03-11 13:30:00, 2024-03-11 13:35:00] UTC
```

The one-hour shift across the DST boundary, visible in the log. This is the kind of test that doesn't fit in a unit test but is immediate in a run.

---

## The Accumulator Timing

The accumulator computes per-symbol statistics used in the signal: hit rate, average positive return, and current winning streak. These are calculated from overnight returns — the move from p1552[T-1] to p0935[T].

That overnight return settles at 09:35. The decision is made at 15:52. Six hours pass between settlement and decision, which means the accumulator is unambiguously past at decision time. The timing here is fine.

What I wanted to verify was that `SPLIT_THRESHOLD` filtering on accumulator updates wasn't silently zeroing genuine large moves and making the statistics cleaner than reality. It is. Moves larger than 20% are excluded from hit\_rate and streak computation. A genuine overnight gap — say a biotech FDA verdict that opens +40% — would be filtered out, making hit\_rate slightly higher than it would be in live trading. This is a known bias, acknowledged in the docstring. It's small and doesn't affect P&L. But it's worth naming.

---

## The Percentile Gate

Every day, the signal for the best candidate is computed. If only signals above the N-th historical percentile trigger a trade, you need to compute that percentile from prior signals only — not including today's.

The original code appended the signal to `signal_history` before checking the threshold. Today's signal appeared in its own gate. This is look-ahead: on a day when the best signal is unusually large, the percentile threshold would automatically rise to partially block it, then immediately include it anyway.

The fix is one line of reordering — append after the decision:

```python
# WRONG: append before threshold check
signal_history.append(best_top_signal)
threshold = np.percentile(signal_history[-252:], min_signal_pctile * 100)

# CORRECT: check, then append
threshold = np.percentile(signal_history[-252:], min_signal_pctile * 100)
use_trade = best_sig >= threshold
# ... make decision ...
if best_top_signal is not None:
    signal_history.append(best_top_signal)
```

The wall-clock annotation makes this obvious: `signal_history` must contain days `0..T-1` at the time of the check. Today's signal doesn't exist yet — the decision hasn't been made.

---

## The HMM Smoother

The regime model is a two-state Gaussian HMM fitted on FRED macro data. At each step it calls `predict_proba(data)` and reads `probs[-1]` — the probability for the last row.

Gaussian HMMs use the forward-backward algorithm. The forward pass sees only past data. The backward pass propagates from the future backward. Reading the last element of a forward-backward sequence would normally leak future state, since β[T] is informed by all observations.

It doesn't here, for a specific reason: at the terminal step, `β_T[i] = 1` for all states by definition. The boundary condition of the HMM algorithm is that the backward variable at the last time step is uniform. So `probs[-1] = α_T ⊙ β_T = α_T ⊙ 1 = α_T`. The last probability is forward-only. Future observations don't reach it.

This is the kind of thing you verify by reading the algorithm, not by checking the code. The code could be calling `predict_proba` correctly and still leaking future state if the algorithm used final `β_T[i] = 0` for unobserved states instead of the standard uniform boundary. The standard boundary saves you. Worth knowing.

---

## Split Contamination vs. Genuine Crashes

The original settlement code filtered large overnight moves:

```python
raw_ret = exit_price / entry - 1
if abs(raw_ret) < SPLIT_THRESHOLD:
    day_ret = raw_ret - 2 * TC
```

If `abs(raw_ret) >= 0.20`, `day_ret` stays at zero. The reason this was added: stock splits cause fake overnight moves of 50%-90%. NVDA did a 10:1 split in June 2024 — the raw overnight "return" from pre-split p1552 to post-split p0935 was approximately -90%. That's a phantom loss, not a real one.

But the filter was too broad. It silently zeroed every large overnight move, including genuine ones. A position in a company that dropped 40% on earnings would book a 0% return rather than a -40% loss. The simulation was quietly giving itself a free pass on the worst trading days.

The fix required knowing which symbols split on which dates. I built `KNOWN_SPLIT_DATES` — a dict mapping each symbol to the specific settlement dates where a split adjustment occurred:

```python
KNOWN_SPLIT_DATES: dict[str, set[date]] = {
    "GOOGL": {date(2022, 7, 18)},   # 20:1 forward split
    "AMZN":  {date(2022, 6, 6)},    # 20:1 forward split
    "TSLA":  {date(2022, 8, 25)},   # 3:1  forward split
    "NVDA":  {date(2024, 6, 10)},   # 10:1 forward split
    # ... 9 more
}
```

Settlement logic now branches on this:

```python
is_known_split = today in KNOWN_SPLIT_DATES.get(sym, set())
if is_known_split and abs(raw_ret) >= SPLIT_THRESHOLD:
    day_ret = 0.0          # share-count adjustment, real value unchanged
else:
    day_ret = raw_ret - 2 * TC  # actual return — no blanket suppression
```

Known split date + large move → zero. Every other date + any size move → book it. The SPLIT\_THRESHOLD filter still applies to accumulator updates (for statistics), but not to P&L.

This solves the backtest contamination for the known 2022–2026 history. It is not a permanent solution. `KNOWN_SPLIT_DATES` is another hardcoded list that will go stale the next time a symbol in the universe splits. For forward use, this should come from a corporate-actions feed or a split table, not from a dict in source code.

---

## The Halted Stock Free Pass

The previous code for a missing exit price:

```python
if exit_price is not None and entry > 0 and exit_price > 0:
    ...
elif do_audit:
    print(f"[SETTLE] {today}: {sym} — no exit price (halted/delisted)")
pending = None
```

When a stock halts or gets delisted, `p0935_today.get(sym)` returns `None`. The `elif do_audit` branch prints a message and moves on. `day_ret` stays at zero.

This is optimistic. If you're holding a stock when it's suspended from trading, you can't exit. In reality you're stuck and the position moves against you. Setting the return to zero is the best possible outcome, which is wrong.

The conservative replacement:

```python
else:
    # No exit price: stock halted, suspended, or delisted.
    day_ret = -SPLIT_THRESHOLD - 2 * TC  # maximum expected loss + transaction costs
    print(f"[HALT] {today}: {sym} — no exit price, penalty {day_ret * 100:.2f}%")
```

A 20% fixed loss plus transaction costs. Harsh, but honest. In four years of simulation, this triggered zero times — the universe consists of large-cap stocks unlikely to halt over a single night. The impact on results is unmeasurable. But the previous code was logically wrong, and wrong logic should be fixed regardless of whether it currently matters.

---

## The Hardcoded Date

```python
trading_days = get_trading_days_from_db(conn, "2022-01-01", "2026-03-12")
```

That end date was the last day I ran the simulation. It would always stop there, even as new trading days accumulated in the database. A simulation that claims to run through "today" but silently cuts off months ago isn't a live simulation — it's a frozen one.

```python
trading_days = get_trading_days_from_db(conn, "2022-01-01", date.today().isoformat())
```

One line. The simulation now runs through whenever you run it.

---

## What's Left

The wall-clock model doesn't claim the simulation is unbiased. It claims there is no *per-day* look-ahead. These are different things.

Four known biases remain:

**Meta-level model selection.** `BEST_CONFIG` was found by Optuna on the training period. We couldn't have known these hyperparameters in 2022. This is look-ahead at the strategy level, unavoidable unless you start a live account and wait four years. The test period — 2025 to present — has never been touched by Optuna. It's the honest number.

**Universe selection.** 153 symbols chosen by prior exploration on the same dataset. A truly blind test would specify the universe before examining any data.

**Survivorship bias.** The universe is built from symbols that exist, trade, and have clean data in the modern snapshot of the database. Stocks that were delisted, bankrupt, merged away, or otherwise disappeared during the period are underrepresented or absent. That removes some of the worst realized paths from the simulation and almost certainly inflates returns.

**Split threshold for statistics.** The accumulator excludes overnight moves above 20% from hit\_rate and streak computation. This makes the statistics slightly cleaner than live. It doesn't affect P&L.

Acknowledging these is part of the model. A simulation that claims no bias is more dangerous than one that lists its biases explicitly, because the unclaimed ones are the ones that bite you.

---

After applying all of this — correct split handling, halt penalties, DST-safe bounds, verified HMM terminal state, ordered percentile gate — the full-period return moved from +2366% to +2981%. Sharpe went from 2.4 to 2.7. Max drawdown fell from 22% to 15%.

The extra return isn't noise. Most of it comes from removing the blanket `abs(ret) < 0.20` gate on P&L: genuine gap-up nights that were previously zeroed now book their actual gains. The positions that hit +30% on a biotech catalyst were being silently erased. The simulation was pessimistic about upside and optimistic about downside — the exact wrong combination.

The lower drawdown should not be attributed to that one change alone. Removing the blanket gate restores genuine large losses too, so by itself that fix cannot explain both higher returns and lower max drawdown. The drawdown improvement comes from the combined bundle of changes changing the realized trade path — especially trade selection and settlement handling together. Without an explicit ablation run, I can't honestly assign the drawdown improvement to any single fix.

The test period Sharpe is 2.92. Higher than the training period (2.62). That's encouraging, but not proof. Out-of-sample beating in-sample should make you more suspicious, not less. It may just mean the 2025–2026 regime happened to suit the strategy unusually well. It may also mean there is still a bug the wall-clock model didn't catch. The wall-clock model removes one important class of errors. It does not grant innocence.

---

## Update (2026-03-15): The Wall-Clock Model Was Necessary But Not Sufficient

The wall-clock model caught every temporal bug. PhasedDay enforcement, pending_row assertions, signal-history ordering — all verified clean across 194 experiments.

But a new class of bugs was hiding in plain sight: **accounting errors** (C-class). These don't violate temporal causality — every data access is correct, every decision uses only past data. Instead, they corrupt the equity math:

- **C2**: Two overnight positions (base stocks + gold) both deploying 100% of capital = 200% leverage in a debit-only system
- **C5**: Daily returns computed additively while equity compounds multiplicatively (cross-term lost)
- **C8**: SQQQ + gold overnight overlapping at 200% on VXX spike days

The wall-clock diagram from this exploration correctly identified WHEN each price was available. But it didn't ask HOW MUCH capital was being deployed at each checkpoint. A strategy can have perfect temporal hygiene and still report impossible returns because it spends more money than it has.

The accounting audit checklist (C1-C10) now accompanies the temporal checklist (A1-R2) for every production experiment. The honest return after both audits: approximately +2,750% (down from +3,883% with only temporal fixes). The temporal bugs were caught by the wall-clock model. The accounting bugs required a different lens entirely.
