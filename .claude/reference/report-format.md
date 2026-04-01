# TEMPORAL_PROOF.md Report Format

Complete report template for STEP 4. Produce all sections in order.

---

## Required Sections

1. **Wall-clock diagram** (see `reference/wall-clock-model.md`)
2. **Temporal audit table** — every data access with causal evidence. Must include ALL instruments/position types. Any instrument without an audit row is a hard stop.
3. **Split filter proof** — code references where split adjustment is applied on BOTH sides
4. **C-class accounting checklist** (see `rules/accounting-correctness.md`)
5. **Results table:**

| Period | Sharpe | Annual Return | Max Drawdown | Win Rate | N Trades |
|--------|--------|--------------|-------------|----------|----------|
| Train | | | | | |
| Test | | | | | |
| Full | | | | | |

6. **Train/test consistency analysis**
7. **Statistical robustness results** (advisory, from `shared/stat_tests.py`)
8. **Commit gate matrix** (see `rules/prompt-compliance.md`)

---

## Evidence Standards

- Every claim must have a concrete reference: file:line, output snippet, or computed value
- "I checked and it looks right" is NEVER valid evidence
- Quantitative claims must show actual numbers (e.g., "20 dates tested, max delta = 2.1e-14")
