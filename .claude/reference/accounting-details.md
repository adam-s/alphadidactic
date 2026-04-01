# Accounting Correctness — Detailed Guidance

Full case descriptions, detection patterns, and examples for C-class checks. The mandatory principles are in `rules/accounting-correctness.md`. This file provides the "how" — read it when implementing or reviewing.

---

## C-exit: Missing Price Handling (Full Cases)

A missing price at a specific time is NOT the same as a delisted stock. A gap in minute-bar data means no trade happened during that minute — the stock is still trading.

### Case 1: Minute-bar gap (no bar at target time, but stock traded that day)

Resolve using same-day price search:

1. **Look earlier first.** Find the most recent bar before the target time on that same day — causally safe.
2. **If nothing earlier, look later.** Find the next available bar after the target time on that same day.
3. **Must be same day.** Never cross day boundaries for price fills.

CursorEngine's `AT_OR_BEFORE` mode with `grace_minutes_before` implements step 1. If no same-day bar exists in either direction, escalate to Case 2.

### Case 2: No same-day price at all (halt, delist, true data gap)

No same-day price = maximum loss. Never silently skip (survivorship bias). Never substitute 0% return. Never `dropna` on exit prices in dataset construction. For dataset-builder experiments, C-exit applies at dataset construction, not just strategy execution.

### Case 3: Missing entry price

Apply Case 1 (same-day search) first. Only if no same-day price exists at all: `day_ret = 0.0` (non-trade, position never established).

**Key distinction:**
- Missing entry, resolution fails → non-trade (`day_ret = 0.0`)
- Missing exit, resolution fails → max loss (position WAS entered)

**Overnight strategies:** Close = ENTRY, next-day open = EXIT. A missing next-day open when `prev_decision` was True is a missing EXIT (max loss), not a missing entry (non-trade).

**Decision tree (MUST follow in order):**
1. Is the exit price missing? → Try same-day resolution (CursorEngine `AT_OR_BEFORE` with grace window)
2. Same-day resolution found a price? → Use it (Case 1, NOT max loss)
3. No same-day price at all? → Max loss (Case 2)
4. Never skip to max loss without attempting same-day resolution first. CursorEngine with `grace_minutes_before=390` handles Case 1 automatically — if it returns None, THEN it's Case 2.

---

## C-TC: Transaction Cost Details

**TC must be proportional to realized turnover.** For daily strategies: `day_ret -= 2 * TC` per holding day. For less frequent rebalancing: `tc_cost = turnover * TC` where `turnover = sum(abs(new_weight - old_weight))`. A flat `2 * TC` on every rebalance day assumes 100% turnover. The invariant: total TC paid = total capital traded × TC rate.

**Detection:** Grep for `TC = 0`, `tc = 0`, `cost = 0`, or hardcoded costs. Check the TC variable is actually applied (not just imported). For non-daily strategies, verify TC scales with actual turnover.

**Sharpe annualization must match trading frequency.** Shared metrics assume `sqrt(252)`. For strategies at different frequencies, override the annualization factor. Document the factor used and why.

---

## C-split: Magnitude Filter Details

**Prefer the authoritative source:** Use `stock_splits` table directly. Magnitude proxies fail at boundaries (2:1 reverse split → -49.6%, slips under 50% threshold). The authoritative ledger is exact.

**Magnitude proxy as fallback only:** Scale threshold to the instrument's expected range.

**A13 applies:** Verification code (Check 6) must NOT clone the strategy's magnitude filter. See `rules/experiment-checks.md` Check 6.

**Event strategies:** Set threshold above expected event magnitude, not at normal-trading levels.

**Splits work in both directions.** Forward splits reduce price; reverse splits increase it (common in leveraged ETFs). Use `abs(return) > threshold`. The authoritative ledger handles direction automatically.

**Distinguish split from missing price.** Before neutralizing to 0.0, check: was exit price None/NaN (missing → C-exit Case 2 = max loss) or present but suspicious (data corruption → neutralize to 0.0)?

---

## C-sizing: Position Sizing Details

**The bug:** Scanning the full realized position schedule to compute `max_global_concurrent`, then using `1/max_concurrent` as a fixed fraction. Forward-looking sizing uses future info AND dilutes returns by allocating for a worst-case that may occur years later.

**Fixes:**
1. **Structural bound:** If strategy parameters imply a theoretical max, use that. Document the derivation.
2. **Dynamic sizing:** `fraction = 1 / (open_positions + new_entries_today)` — point-in-time correct.
3. **Never scan the realized position schedule.**

---

## Check 6: Scaling Rules and Evidence Integrity

**Scaling rules (apply when relevant):**

- **Sparse events (< 20 total):** Verify ALL events. 12/12 is stronger than 20/252.
- **Multi-asset:** 20 dates for one symbol + 5 each for others.
- **Multi-strategy/regime:** Verify ALL claimed equity curves.
- **Dataset-builder:** Also verify temporal routing in the dataset.
- **Event-driven:** Independently resolve temporal neighbors — don't trust cached date columns.

**Evidence integrity:**

- **Documentation must match implementation.** Grep Check 6 for data source references and verify each matches the claimed source.
- **Fabricated evidence is a hard stop.** Reviewer must spot-check 2+ quantitative claims.

---

## Check 7: Extended Methodology

**Regime-switching:** Evaluate each regime against its hypothesized direction. Document which regimes are inverted in PRE-FLIGHT. The unconditional (FULL) strategy must still pass the opposing-directions check. Use within-regime signal-on vs signal-off returns (not all-days mean, which is diluted by non-regime days).

**Use the SAME return handling as the strategy.** If the strategy neutralizes splits to 0%, the direction test must too.

**Compare against the unconditional benchmark.** All Sharpe ratios in the same comparison must use the SAME function with the SAME TC treatment. Report TC-free comparison alongside TC-adjusted.

**Single-sided vs double-sided transitions (C-TC):** `2 * TC` models a round-trip (exit old + enter new). For single-sided transitions (exit to cash, or enter from cash), use `1 * TC`.

**Sharpe annualization must match trading frequency.** `sqrt(252)` assumes daily. Override for non-daily strategies.

---

## Scoring

See `agents/research-reviewer-agent.md` for the scoring rubric (C2→C1, C5→C2, C-exit→C3, C-TC→C4, max 8 points).
