# Pending-Row Pattern

## The Only Allowed Return-Pairing Pattern

ALL code that pairs a signal with a return MUST use the pending-row pattern. No exceptions. No simplified versions. Not even in validation scripts, audit scripts, or one-off checks.

```python
# THE CANONICAL PATTERN
prev_decision = None
for T in trading_dates:
    ovn_ret = p0935[T] / p1552[T-1] - 1          # today's overnight return

    if prev_decision is True:
        day_ret = ovn_ret - 2 * TC                  # yesterday's decision earns today's return
        equity *= (1 + day_ret)                      # multiplicative compounding

    prev_decision = signal[T]                        # today's signal decides tomorrow's position
```

### Equivalent Implementation: Dataset Builder

The pending-row concept can also be implemented as a dataset builder that attaches `next_*` return columns during day-by-day iteration:

```python
# ALSO VALID — dataset-builder approach
pending_row = None
for T in trading_dates:
    prices = get_prices(T)
    if pending_row is not None:
        # Fill yesterday's row with today's settlement
        pending_row["next_open"] = prices["p0935"]
        pending_row["next_return"] = prices["p0935"] / pending_row["close"] - 1
        rows.append(pending_row)
    pending_row = {"date": T, "close": prices["p1600"], "signal": compute_signal(T)}

# Then in strategy loop:
for row in rows:
    if row["signal"]:
        equity *= (1 + row["next_return"] - 2 * TC)
```

Both approaches are valid because both ensure signal[T] is paired with return[T+1], never return[T]. The `prev_decision` loop is more explicit; the dataset-builder approach separates data from strategy. Choose whichever is clearer for your experiment.

**C-exit in dataset-builder strategies:** When the dataset pre-joins next-day prices, a `None` in the next-day column is ambiguous — it could mean (a) missing entry price on the anchor day (non-trade, 0%) or (b) entry price present but exit price missing (max loss, -100%). The strategy MUST check BOTH conditions independently:

```python
if entry_price is None:
    day_ret = 0.0                   # non-trade — no position was entered
elif exit_price is None:
    day_ret = -1.0                  # C-exit: entered but can't exit → max loss
else:
    day_ret = exit_price / entry_price - 1
```

A common bug: the entire settlement block is gated by `if entry is not None and exit is not None`, silently skipping the missing-exit case.

**Standard pattern for missing exit prices:** Use `settle_price_fallback()` from `shared/cursor_engine.py`. It searches earlier-first on the same day (the data collector skips bars when price is unchanged, so earlier bar = current price). Log all fallback resolutions to `data_gaps.json` for review. See `reference/shared-infrastructure-guide.md` § Settlement price fallback.

---

### Why This Pattern

The key insight: **today's signal cannot earn today's return.** The signal at 16:00 ET cannot affect a return that settled at 09:35 ET the same day. The decision must precede the settlement.

- `prev_decision` holds yesterday's signal
- `ovn_ret` is today's overnight return (settles at 09:35 today)
- Yesterday's decision earns today's return
- Today's signal becomes tomorrow's `prev_decision`

This one-row lag is what makes the signal causal.

---

## The Point-in-Time Principle (Critical)

**At wall-clock time T, you have all data ≤ T. You compute and trade at T. Never use data > T.**

This is the fundamental temporal correctness rule. A real trader at 9:05 ET sees the 9:00, 9:01, 9:02, 9:03, 9:04, and 9:05 bars. They compute a signal and execute at 9:05. The 9:06 bar, the 13:30 bar, and anything later does not exist yet. That's the only constraint.

The pending-row lag exists because overnight strategies have a specific temporal inversion: the overnight return settles at 09:35 ET *before* the signal is computed at 16:00 ET. Without the lag, signal[T] would earn a return that already happened. The lag fixes this by pairing signal[T] with return[T+1].

**Intraday strategies have no inversion.** Time flows forward within the day. A signal computed at 10:30 from data ≤ 10:30 can trade at 10:30 and earn a return from 10:30 onward. No lag needed — the point-in-time principle is satisfied directly.

```python
# VALID for intraday — compute and trade at 10:30, earn return from 10:30 to 15:52
for T in trading_dates:
    morning_ret = p1030[T] / p0935[T] - 1        # uses data at 9:35 and 10:30, both ≤ 10:30
    signal = morning_ret > threshold               # decision at 10:30 ET

    if signal:
        pm_ret = p1552[T] / p1030[T] - 1          # return accrues AFTER decision
        equity *= (1 + pm_ret - 2 * TC)
```

### Optimization: pre-fetch specific checkpoints

You don't need every minute bar. If your strategy only needs 15 specific timestamps (9:35, 10:03, 10:04, 13:40, 13:50, ...), fetch only those into a table. This is valid as long as:
1. Every computation uses only data at or before its wall-clock moment
2. The wall-clock diagram maps each checkpoint to a concrete ET time
3. The audit (STEP 3) verifies point-in-time correctness on the pre-fetched table

The pre-fetched table is an optimization, not a bypass. The integrity checks still apply.

### When the pending-row lag IS required

The lag is required when the return settles *before* the signal on the same calendar day:
- Signal at 16:00 ET, return settled at 09:35 ET → **inversion** → lag required
- Signal at 10:30 ET, return from 10:30 to 15:52 ET → **no inversion** → no lag needed
- Signal at 15:00 ET, return from 15:00 to 15:52 ET → **no inversion** → no lag needed

The test: does the return's settlement time precede the signal's computation time on the same calendar day? If yes, you need the pending-row lag. If no, point-in-time is sufficient.

---

## Anti-Pattern 0: Double-Lag (Dataset Builder + Strategy Loop)

```python
# BANNED — dataset already embeds the return lag, strategy adds another
# Dataset builder:
pending_row["ovn_ret"] = p_open[T] / pending_row["p_close"] - 1  # signal[T-1] → return[T-1→T]
# Strategy:
if prev_decision is True:       # prev_decision is from row T-1
    day_ret = row[T]["ovn_ret"]  # but ovn_ret[T] is the return from T→T+1, NOT T-1→T
```

**Why it's wrong:** The dataset builder pairs `signal[T]` with `return[T→T+1]` and stores it in row T. The strategy's `prev_decision` pattern then reads row T but applies the return to the *previous* row's decision, meaning `signal[T-1]` earns `return[T→T+1]` — skipping one night entirely. Every trade earns the wrong night's return.

**The rule:** Choose ONE lag mechanism, not both:
1. **Dataset-builder approach:** Dataset pairs `signal[T]` with `return[T→T+1]` in the same row. Strategy uses same-row pairing (`if signal[T]: earn ovn_ret[T]`). No `prev_decision` needed.
2. **Strategy-loop approach:** Dataset stores raw prices. Strategy uses `prev_decision` to lag the signal by one iteration and computes returns from raw prices.

Mixing them creates a double lag that shifts every trade by one period. This bug is invisible to Check 6 if Check 6 only verifies the dataset (not the strategy equity chain).

---

## Anti-Patterns (BANNED)

### Anti-Pattern 1: Same-Day Pairing
```python
# BANNED — signal[T] paired with return[T]
for T in trading_dates:
    if signal[T]:
        ret = p0935[T] / p1552[T-1] - 1
        equity *= (1 + ret - 2*TC)
```
**Why it's wrong:** `signal[T]` is computed at 16:00 ET day T. `p0935[T]` settled at 09:35 ET day T — 6.5 hours *before* the signal existed. This is Exploration 33: TSLA +7,764% → +177%.

### Anti-Pattern 2: Vectorized Pairing
```python
# BANNED — vectorized operations hide the temporal relationship
returns = df['close'].pct_change()
positions = (df['signal'] > threshold).astype(int)
strategy_returns = positions * returns
```
**Why it's wrong:** `positions` and `returns` are aligned by index. Same-day pairing. The vectorization makes it look clean but the bug is the same as Anti-Pattern 1.

### Anti-Pattern 3: Simplified Audit Loop
```python
# BANNED — simplified version for "just checking"
for T in trading_dates:
    if signal[T]:
        audit_ret = compute_return(T)  # same-day return
        audit_equity *= (1 + audit_ret)
```
**Why it's wrong:** This is Exploration 35. The audit script introduced the same bug it was checking for. The simplified loop lacks the `prev_decision` lag. The verifier verified itself.

### Anti-Pattern 4: Shift Without Lag
```python
# BANNED — shift(1) on returns but not on signal
df['strategy'] = df['signal'] * df['return'].shift(-1)
```
**Why it's wrong:** `shift(-1)` on returns looks like it's using tomorrow's return, but the alignment depends on how `return` is defined. If `return[T]` is the return *from* T to T+1, then `signal[T] * return[T]` is correct but `signal[T] * return[T].shift(-1)` is wrong. The pending-row pattern makes the temporal relationship explicit and unambiguous.

---

## In Benchmarks

Buy-and-hold or market benchmarks MUST use the same temporal alignment as the strategy. If the strategy uses pending-row (yesterday's decision earns today's return), the benchmark must also lag by one day. A benchmark that applies `return[T→T+1]` at iteration T while the strategy applies `return[T-1→T]` is temporally misaligned — the benchmark includes one extra day of return.

```python
# CORRECT benchmark — same pending-row alignment as strategy
bh_equity = [initial_capital]
for T in trading_dates[1:]:
    bh_ret = close[T] / close[T-1] - 1  # settled return, same as strategy
    bh_equity.append(bh_equity[-1] * (1 + bh_ret))
```

```python
# BANNED — uses next_close (forward-looking relative to strategy)
for T in trading_dates:
    bh_ret = next_close[T] / close[T] - 1  # includes T+1 price at iteration T
    bh_equity *= (1 + bh_ret)
```

---

## In Verification Scripts

Verification scripts (`verify_integrity.py`) MUST use the same pending-row pattern. The verification loop must have `prev_decision`. Any "simplified" verification loop will inherit the bug it's checking for (Exploration 35).

```python
# CORRECT verification — uses the same pattern
def verify_returns(signals, prices, trading_dates, TC):
    prev_decision = None
    verified_equity = 1.0
    for T in trading_dates:
        ovn_ret = prices['p0935'][T] / prices['p1552'][T-1] - 1
        if prev_decision is True:
            day_ret = ovn_ret - 2 * TC
            verified_equity *= (1 + day_ret)
        prev_decision = signals[T]
    return verified_equity
```

---

## Incremental Truncation Test

The incremental test (STEP 3, check #6) must also use the pending-row pattern:

```python
for T in sample_dates:
    # Truncate ALL data at T
    truncated_prices = prices[prices.index <= T]
    truncated_signals = signals[signals.index <= T]

    # Compute signal using ONLY truncated data — pending-row pattern
    prev_decision = None
    for t in truncated_prices.index:
        ovn_ret = truncated_prices['p0935'][t] / truncated_prices['p1552'][t-1] - 1
        if prev_decision is True:
            # ...
        prev_decision = truncated_signals[t]

    # Compare with batch result at T
    assert abs(incremental_equity[T] - batch_equity[T]) < 1e-8
```

## Benchmark Alignment

The benchmark must earn the SAME type of return as the strategy. If the strategy earns overnight returns (close[T] to open[T+1]), the benchmark must also use overnight returns. A close-to-close benchmark includes intraday returns the strategy never captures, making comparison misleading.
