# Temporal Correctness — Mandatory Gate

No experiment may be committed without passing every check below.

---

## Temporal Audit Table

Every experiment must produce a temporal audit table. One row per data access:

| # | Data Access | Available At (ET) | Used At (ET) | Causal? | Evidence |
|---|------------|-------------------|-------------|---------|----------|

**Rules:** Every row must have Causal = Y. "Available At" must be a concrete wall-clock time in ET.

---

## Core Principle

**At wall-clock time T, you have data ≤ T. Never data > T.** The signal that triggers a position must precede the return that settles it.

---

## Known Bug Classes

See `reference/bug-catalog.md` for the full catalog (A1-A14, R1-R6). Key ones:

- **A1 (Timezone):** Use CursorEngine — it handles dual timezone conversion by construction.
- **A2 (DISTINCT ON):** Use single-day queries — CursorEngine enforces this.
- **A3 (Same-Day Pairing):** Use the pending-row pattern (`reference/pending-row-pattern.md`).
- **A13 (Self-Referential Audit):** Check 6 must independently verify, not clone the strategy.
- **A14 (Sign Convention):** A signal is valid if all its inputs existed before the computation. Negating, inverting, or transforming a causal signal produces another causal signal. Do not flag a sign convention as a bug unless it violates temporal causality.

---

## Split Handling

Split filters on BOTH signal and return sides using `is_split(abs(r) >= SPLIT_THRESHOLD)`. For missing exit prices, use `settle_price_fallback()` (earlier-first same-day search). See `reference/shared-infrastructure-guide.md` § Settlement price fallback.

---

## Dual Timezone Conversion

CursorEngine handles this internally. For raw SQL (only in pre-aggregated loaders): `((col AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time`. Any `::time` without dual conversion is wrong.
