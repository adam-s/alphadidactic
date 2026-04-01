# The Kernel Design

The kernel fixes the bias by computing the correlation incrementally. At each day T, the kernel only uses data from day 0 through day T. No future rows. No full-period summary statistics. The signal at T is what you could have known at T close.

---

## The Core Loop

```python
for T in range(min_obs, n):
    x = flow[:T]           # flow[0..T-1] — known at T close
    y = return_pct[:T]     # return[0..T-1] — known at T close
    ...
```

At index T, the slices `flow[:T]` and `return_pct[:T]` contain rows 0 through T-1. The most recent pair is `(flow[T-1], return_pct[T-1])`.

`return_pct[T-1]` is `(price[T] / price[T-1] - 1) × 100` — today's overnight return. You know this at T close. There is no forward reference.

The correlation this loop computes is the Spearman rank correlation over those T observations, with exponential decay applied: recent pairs count more than old ones. The half-life is 246 trading days, roughly one year. A pair from 500 days ago contributes about one-quarter of the weight of a pair from today.

---

## Why Exponential Decay

A pure expanding average would have the 2022 observations drag on the 2025 correlation indefinitely. The relationship between retail flow and returns is not stationary. Market regimes shift. A 2022 correlation estimate is less informative about the 2025 relationship than a 2024 estimate.

The half-life of 246 was found through Optuna optimization on the training period. It was not tuned on test data — the train/test split was set at December 31, 2024, and Optuna ran exclusively on data before that date.

---

## The EMA Smoothing Layer

After the Spearman series is computed, a z-score is applied within a rolling window, and then an EMA with span 6 smooths the noisy daily estimates. This is still backward-looking — z-scores and EMAs only use past values by construction. The EMA at day T is a weighted average of the signal through day T, with exponentially decaying weights back into the past.

---

## Six Features, One Signal

The kernel runs independently on each of the six flow features. Each produces its own correlation series. The composite signal is a weighted average:

```python
composite = sum(
    coeff[name] * sign(corr[name]) * zscore[name]
    for name in FEATURE_NAMES
    if abs(corr[name]) >= MIN_CORR[name]
)
```

Features whose correlation estimate hasn't reached the minimum threshold contribute zero. Features in disagreement with the majority direction are dampened by a dissent factor. The result is a single number on each day: positive means the aggregate flow pattern is bullish for QQQ, negative means bearish, zero means no signal.

---

## The Adaptive Threshold

Shorting is gated by a vol-scaled threshold:

```
adaptive_threshold = SHORT_THRESHOLD × (rvol_median / rvol_20d)
```

When realized volatility is high relative to its recent median, the threshold contracts: it takes less signal strength to enter a short position. When volatility is low, the threshold expands. Both `rvol_20d` and `rvol_median` are rolling backward-looking statistics — the 20-day rolling standard deviation and a 120-day rolling median of that. No future data enters.

---

## What Makes This Different from 01_

The original correlation study computed one number — ρ over 2022–2026 — and used it for all 1,009 trading days. The kernel computes a different number for each trading day, using only the data that preceded that day.

On day 100 of the dataset, the kernel's correlation estimate is based on the 100 (flow, return) pairs seen so far, with exponential decay. On day 500, it is based on 500 pairs. On day 1,009, it is based on 1,009 pairs — and at that point, it happens to be +0.057 for `retail_2_3dte`. The study would have used +0.086. The 0.029 gap is exactly the look-ahead: the extra correlation signal from the 200+ post-training days that the study knew about and the kernel did not.

---

## Next

[03 — Signal to Strategy](03-signal-to-strategy.md): How the kernel feeds into the full production pipeline.
