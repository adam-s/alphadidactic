# Bug Catalog: Explorations 31-35

Every bug below produced results that looked correct. Internal consistency, clean equity curves, plausible Sharpe ratios. They were caught only by adversarial validation or accident.

**These are stories, not rules.** The authoritative rules live in the files referenced at the bottom of each entry. Read the stories to understand *why* each rule exists.

---

## A1: Timezone Cast — Exploration 31

**Story:** `time::time` applied to UTC timestamps returned UTC wall-clock times. All prices shifted 4-5 hours. 20 scripts, 40+ queries, all wrong the same way. Internal consistency masked the error — every query was broken identically, so spot checks passed.

**Impact:** KZ signal Sharpe +2.6 → -83% after fix.

**General principle:** Any time-of-day extraction applied to a timestamp without explicit timezone conversion will return the database's native representation (usually UTC), silently shifting all times by the UTC offset. This applies to any database, any timezone, any extraction function — not just PostgreSQL's `::time`.

**Authoritative rule:** `rules/temporal-correctness.md` § Dual Timezone Conversion

---

## A2: DISTINCT ON Mirage — Exploration 32

**Story:** `DISTINCT ON (symbol)` across 1,016 TimescaleDB chunks returned random rows depending on which chunk the query planner hit first. 91.5% of prices were wrong. Spot checks used different query plans and returned correct rows.

**Impact:** XGBoost Sharpe 3.69 → 0.03 after fix.

**General principle:** Any deduplication or "pick one row per group" operation across partitioned data is non-deterministic unless the query is constrained to a single partition. This applies to any partitioned database, not just TimescaleDB — Spark, BigQuery, and DuckDB all have analogous partition-boundary effects.

**Authoritative rule:** `rules/database-safety.md` § single-day bounds; `reference/shared-infrastructure-guide.md`

---

## A3: Same-Day Return Pairing — Exploration 33

**Story:** Signal at 15:52 paired with return settled at 09:35 the same day (6 hours earlier). The return was already realized before the signal existed. The code looked correct — `signal[T]` and `return[T]` share the same date index. The bug is temporal, not logical.

**Impact:** TSLA +7,764% → +177% after fix.

**General principle:** Any computation that pairs a decision with an outcome must verify that the outcome's settlement time is strictly after the decision time in wall-clock, not just calendar date. This applies to any frequency — daily, intraday, weekly. The point-in-time principle (rule #1) is the generalization.

**Authoritative rule:** `reference/pending-row-pattern.md` § Point-in-Time Principle

---

## A13: Self-Referential Audit — Exploration 35

**Story:** The verification code used a simplified loop that introduced the same bug it was supposed to detect. The verifier verified itself — both paths had identical bugs, so they agreed perfectly.

**Impact:** GLD EMA Sharpe 3.913 → 2.057 after fix.

**General principle:** Any verification that shares code, logic, or intermediate state with the system it verifies cannot detect bugs in that shared path. Independent verification means independent implementation — different code computing the same result from the same raw inputs. This applies to all verification, not just Check 6: signal direction checks, benchmark comparisons, and accounting audits are all susceptible.

**Authoritative rule:** `rules/experiment-checks.md` § Check 6 (A13 WARNING)

---

## R1: Percentile Gate Ordering — Exploration 34

**Story:** A percentile gate computed its threshold from data that included the value being gated. The gate's threshold depended on the signal it was gating — circular dependency.

**General principle:** Any gate, filter, or threshold that conditions on the value it's filtering creates a circular dependency. The gate's inputs must be available strictly before the value being gated. This generalizes to z-score standardization (don't include the current value in its own window), cross-sectional rankings (don't include the current period in the ranking period), and regime classification (don't use the current regime to define itself).

**Authoritative rule:** `reference/wall-clock-model.md`; `rules/experiment-checks.md` § Rolling window self-inclusion check

---

## R2: Blanket Split Filter — Exploration 34

**Story:** Entire dates removed instead of prices adjusted for splits. Survivorship bias — non-split dates overrepresented.

**General principle:** Any data-cleaning operation that removes observations instead of correcting them introduces survivorship bias. The principle applies beyond splits: removing outliers, dropping NaN rows, filtering "bad data days" — all bias the sample toward clean observations. Correct the data or flag it; don't remove it.

**Authoritative rule:** `rules/temporal-correctness.md` § Split Handling; `rules/accounting-correctness.md` § C-exit

---

## R3: Halt/Delisted Free Pass — Exploration 34

**Story:** Missing exit prices silently skipped. Only successful exits counted.

**General principle:** Any position that is entered but cannot be exited must be accounted for as a loss, not silently dropped. This applies to any missing data at exit time: halts, delistings, data gaps, half-day closures. Silently skipping = survivorship bias. See also the double-lag anti-pattern (Anti-Pattern 0 in pending-row-pattern.md) where skipping is introduced indirectly.

**Authoritative rule:** `rules/accounting-correctness.md` § C-exit

---

## R7: CachedPhasedDay None Overwrite — Experiment 18

**Story:** `CachedPhasedDay.resolve_up_to()` returns `None` for symbols without a price at a checkpoint. When building a fallback dict by iterating checkpoints (latest-wins), a `None` at p1600 overwrites a valid price from p1400. The tape dict filters Nones, so run_strategy and Check 6 diverge.

**General principle:** When iterating `CachedPhasedDay` resolved prices into a fallback dict, always filter Nones: `if p is not None: prev_close[sym] = p`. The tape-building code in run_strategy already filters Nones; the verify code must do the same.

**Authoritative rule:** No existing rule covers this. Pattern-level vigilance required.

---

## R6: Dict-Overwrite Fallback — Experiment 15

**Story:** `prev_close` dict built by iterating `["p1600", "p1030", "p0935"]` and overwriting. Last writer wins → p0935 (earliest) instead of p1600 (latest). EMA trained on open-to-open returns, not overnight returns. Both strategy and verify shared the bug, so Check 6 passed.

**General principle:** When building a fallback dict (prefer latest, fall back to earlier), iterate least-preferred first so the most-preferred overwrites last. Or use `dict.setdefault()` with most-preferred first. The iteration order `["best", "good", "fallback"]` with overwrite produces `fallback` — the opposite of intent.

**Authoritative rule:** No existing rule covers this. Pattern-level vigilance required.

---

## C-Class: Accounting Bugs — Exploration 35 Postscript

**Story:** Even after all temporal bugs were fixed, equity math errors inflated returns by ~30%. Position overlap created implicit leverage. Additive vs multiplicative returns diverged.

**General principle:** Temporal correctness and accounting correctness are independent failure modes. An experiment can have perfect causality and still report impossible returns from equity math errors. Both audits must pass separately.

**Authoritative rule:** `rules/accounting-correctness.md` (full checklist)
