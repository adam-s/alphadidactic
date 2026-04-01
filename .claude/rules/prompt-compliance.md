# Prompt Compliance — Commit Gate Matrix

No experiment may be committed unless ALL mandatory checks pass.

---

## Commit Gate

| # | Requirement | Mandatory | Status | Evidence |
|---|-------------|-----------|--------|----------|
| 0 | Temporal audit table filled, all Causal=Y | **HARD GATE** | | |
| 1 | Wall-clock diagram produced | YES | | |
| 2 | Signal-before-return causality enforced for all return pairing | YES | | |
| 3 | All DB queries single-day bounded | YES | | |
| 4 | Split filter on signal AND return sides | YES | | |
| 5 | Incremental vs batch match (high precision) | YES | | |
| 6 | C-class accounting checklist passed | YES | | |
| 7 | 8-step verification completed (Checks 1-8) | YES | | |
| 8 | Train/test Sharpe reported with degradation analysis | YES | | |
| 9 | Signal direction verified (Check 7) in both train and test | YES | | |
| 10 | WebSearch called at least once in STEP 0, results in PREFLIGHT.md | YES | | |
| 11 | Statistical robustness (3 tests run, results in TEMPORAL_PROOF.md) | YES | | |
| 12 | Optuna ran: train-only objective, baseline seeded, re-verified, baseline vs optimized reported (skip for diagnostic grid experiments — document why grid is sufficient) | YES | | |

### Valid evidence:
- `#0: TEMPORAL_PROOF.md § temporal audit table, all rows Causal=Y`
- `#2: run_strategy.py — signal computed before return settled, with specific function/line reference`
- `#5: verify_integrity.py — N dates tested, max delta = X`

### Invalid evidence:
- `#0: "I checked and it looks right"` — must reference specific audit table
- `#2: "Used correct pattern"` — must reference specific code location
- `#5: "Tested"` — must show number of dates and max delta

---

## Failure Protocol

If ANY check fails:
1. Do NOT commit
2. Identify the specific violation
3. Fix the root cause (not a workaround)
4. Re-run the full verification (STEP 3)
5. Re-fill this matrix with updated evidence
6. Only commit when all checks pass

If check #0 (temporal audit) fails, the experiment has a fundamental causality error. Do not attempt to fix it incrementally — review the wall-clock diagram and identify where time flows backward.

---

## Matrix Ownership

The experiment agent fills this matrix as a self-check. The reviewer independently scores against `research-reviewer-agent.md` § Audit Structure (which maps to these gates). **The reviewer's assessment takes precedence.** If the reviewer's scores disagree with the experiment agent's self-reported matrix, the experiment must be fixed until both agree.
