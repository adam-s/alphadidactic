# The Biased Ancestor

The original signal came from `01_correlation_study`. The idea was straightforward: compute the Spearman rank correlation between each options flow feature and QQQ's next-day return. If retail 2–3 DTE flow correlates positively with returns, use it as a bullish signal. If it correlates negatively, flip the sign.

The study ran over the full dataset — 2022 through 2026 — and found that `retail_2_3dte` had ρ = +0.086 with a p-value of 0.007. Statistically significant. Directionally clear. The signal used this positive correlation to go long when retail 2–3 DTE flow was elevated above its z-score threshold.

---

## The Problem

Computing ρ over the full period and then applying it backward to 2022 assumes that on January 3, 2022, you already knew what the retail flow/return relationship would look like through November 2025.

You did not.

The full-period correlation is the correlation you could compute on the last day of the dataset, looking back over everything. In early 2022 — the first few months of data — the true expanding-window correlation might have been positive, negative, or indeterminate. The study didn't ask that question. It took the final answer and applied it to the entire history.

This is the mechanism of look-ahead bias: not future prices leaking into a past calculation, but future *statistics* determining the interpretation of past data. The signal direction in February 2022 was decided by information from 2024 and 2025.

---

## Measuring the Distortion

The actual expanding-window correlation at the end of the train period (December 31, 2024) was +0.057 — noticeably lower than the full-period +0.086. That difference is the bias's fingerprint. The kernel's estimate at train end was computed using only data available through that date. The study's estimate was computed using data from the following year as well.

At a single check point early in the dataset (index 100, roughly early 2024 in the QQQ price series), the full-period study assigns `+` direction. The expanding-window kernel at that same point had a correlation that could have been lower, higher, or opposite in sign — we don't know without running it. That's the point: `01_correlation_study` had already decided.

---

## Why It Looked Convincing

The bias is hard to catch because the test set performance still looked real. The study applied its full-period correlation uniformly, so you couldn't see the look-ahead in any single date's prediction. The signal was just "this feature is positively predictive" applied everywhere. The contamination was structural — baked into the setup, not visible in any row of the output.

Any backtest built on top of `01_correlation_study` inherited this contamination. If a backtest period was 2022–2026 and the correlation was computed over 2022–2026, the train and test periods were both poisoned. There was no clean hold-out.

---

## What This Established

The point of revisiting `01_correlation_study` wasn't to condemn the approach in isolation. It was to establish a baseline: here is a system that we *know* has look-ahead bias, and here is the specific mechanism (full-period ρ applied retroactively). Then when we test the kernel, we can check whether it has the same property.

It doesn't. That's what the rest of this exploration shows.

---

## Next

[02 — The Kernel Design](02-the-kernel-design.md): How the expanding-window kernel replaces the static correlation with a causal one.
