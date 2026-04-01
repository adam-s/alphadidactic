# Signal to Strategy

The kernel composite signal is the first layer. By the time it reaches a live trade, it has passed through three more.

---

## 14_kernel_signal → the signal

`14_kernel_signal/algorithm.py` computes `kernel_composite_signal` from the raw flow features and QQQ close-to-close returns. The output is a daily series from 2022 to 2026: the composite signal value for each trading day.

At this layer, the backtest is simple: if `signal[T] > 0`, go long QQQ; if `signal[T] < 0`, short QQQ. Compound daily. The intraday return at this layer is close-to-close, not intraday — the 14:55 ET exit is a later refinement.

The train/test split is December 31, 2024. Everything before that date was used to optimize the kernel hyperparameters (via Optuna). Everything after is held out.

---

## 15_portfolio → the growth/hedge structure

`15_portfolio` splits the strategy into two legs:

**Growth leg**: Long TQQQ when signal is positive, flat otherwise. The EMA-50 exit overrides the kernel: if QQQ closes below its 50-day EMA, exit regardless of signal. Re-entry waits for QQQ to recross the EMA and for the kernel to be non-negative.

**Hedge leg**: Short SQQQ (i.e., long SQQQ goes down when QQQ goes up — the hedge is inverted) when the adaptive signal is sufficiently negative. SQQQ is the 3× inverse, so shorting SQQQ is equivalent to a 3× leveraged long hedge against a QQQ decline.

The portfolio combines the two legs with a fixed allocation split. The growth leg gets the larger allocation; the hedge leg is sized by `RISK_FRACTION_HEDGE`.

The paranoid exit logic: when the EMA-50 fires an exit on day T (i.e., `exit_signal[T] = True`), the backtest records `exit_today = True` and does not earn `return_pct[T]`. Since `return_pct[T]` is the T-close to T+1-14:55 return — a future return the position won't see — skipping it is correct. The exit happened at close[T], so the next period's return is not earned.

---

## 16_intraday_exit → the production exit timing

`16_intraday_exit` adds the 14:55 ET exit. Every day that the strategy is in position, it sells at 14:55 and re-buys at the close. The daily return earned is `close[T] → 14:55[T+1]`, not `close[T] → close[T+1]`.

The `build_intraday_return_series` function:

```python
out["next_exit"] = ext["price"].shift(-1).values
out["return_pct"] = (out["next_exit"] * split_factor / out["price"] - 1) * 100
```

Row T gets the 14:55 price from row T+1. `shift(-1)` is a look-ahead in the numpy sense (row i contains row i+1's value), but it represents a physically future event that will actually occur. The backtest earns it by holding position from today's close until tomorrow's 14:55. This is not look-ahead bias — the timing is correct and causal.

The kernel trains on close-to-close returns (`return_pct_close`) and the backtest earns close-to-14:55 returns. These are slightly different series. The implication is that the kernel's correlation estimates are based on a different return definition than what's being traded. This is a design choice, not a bias — both series are causal.

---

## The Full Pipeline on a Given Day

On Tuesday at 15:30 ET:

1. Flow data for Tuesday is loaded from the database (option orders through 15:30 ET)
2. Features are built: net flow aggregated by DTE bucket, normalized, z-scored
3. Kernel composite signal is computed using data through Tuesday
4. Adaptive threshold is applied: is the signal strong enough to short?
5. EMA-50 check: is QQQ above its 50-day EMA?
6. If conditions are met, a position (long TQQQ, short SQQQ, or both, or flat) is set
7. The position is held from Tuesday's close to Wednesday's 14:55

None of these steps use Wednesday's data. Every input is available at Tuesday's close.

---

## The Numbers

The research backtest over 2022–2026 returns approximately +175% (see [28 — The Compounding Gap](../28_compounding_gap/README.md) for why this differs from paper trading). The paper trading result, with stock splits handled correctly, is approximately **$163,000** from $100,000 starting capital.

The split accounting matters for TQQQ specifically. TQQQ had a 5-for-1 reverse split in November 2022. A paper trading system that doesn't adjust for splits would compute P&L on the pre-split share count at post-split prices — or vice versa — producing incorrect results. The ~$163K figure reflects corrected split handling.

---

## Next

[04 — Nine Tests, Zero Failures](04-nine-tests.md): How the validation battery was constructed and what it found.
