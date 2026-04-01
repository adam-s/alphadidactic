# The Kernel Signal

The research pipeline for `16_intraday_exit` runs a kernel-based options flow signal against QQQ from 2022 to 2026. The signal combines six flow features — retail 2–3 DTE, retail next-day, block 3-month, ultra 0DTE put, mid 0DTE, and aggregate weekly — into a single composite score using expanding-window exponential-decay Spearman correlations. When the score is positive, the strategy goes long. When negative and strong enough, it hedges.

The question that had to be answered before shipping this to production: does the signal cheat?

It was built specifically to replace an earlier approach that did cheat — `01_correlation_study`, which computed Spearman correlations over the full 2022–2026 dataset and applied them retroactively to all of history. Using the four-year correlation to assign signal direction in month one is look-ahead bias. The kernel was supposed to fix that. But "supposed to" is not the same as "does."

This exploration covers the original bias, the kernel design, the production pipeline, and the nine-test validation battery that was built to answer the question definitively.

---

## The Answer First

Every test passed. 20 randomly selected dates spanning 2022 through 2025 — the gold-standard incremental test — matched to 8+ decimal places between batch and truncated computation. Corrupting all future returns left past signals unchanged. The full signal pipeline (raw kernel → adaptive threshold → EMA-50 exit) showed zero mismatches across 15 test dates. Paper trading with splits correctly handled: **~$163,000** from $100,000 starting capital.

---

## Parts

- [01 — The Biased Ancestor](01-the-biased-ancestor.md): What `01_correlation_study` did and why it can't be trusted.
- [02 — The Kernel Design](02-the-kernel-design.md): How the expanding-window kernel works and why it avoids the bias.
- [03 — Signal to Strategy](03-signal-to-strategy.md): From `14_kernel_signal` through `15_portfolio` to `16_intraday_exit`.
- [04 — Nine Tests, Zero Failures](04-nine-tests.md): The validation battery and what each test covers.

---

## Key Numbers

| Metric | Value |
|--------|-------|
| Full-period Spearman ρ (01_) | +0.086 (biased — unknowable at T) |
| Kernel ρ at train end (14_) | +0.057 (what was actually knowable) |
| Gold standard test dates | 20/20 match to 8+ decimal places |
| Full pipeline incremental test | 15/15 dates, 0 mismatches |
| Existing synthetic test suites | 30/30 PASS (16 for 14_, 14 for 15_) |
| Paper trading (split-adjusted) | ~$163,000 from $100,000 |

---

## Related

- `research/spy_flow_spreads/14_kernel_signal/algorithm.py` — core kernel implementation
- `research/spy_flow_spreads/16_intraday_exit/algorithm.py` — full production pipeline
- `research/spy_flow_spreads/tmp/` — all nine validation scripts
- `docs/temp/look_ahead_bias_analysis.md` — full test run report with output
- [28 — The Compounding Gap](../28_compounding_gap/README.md) — why research and paper trading returns differ
