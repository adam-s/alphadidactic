# Accounting Correctness — C-Class Bugs

Temporal correctness does not imply accounting correctness. Both audits are separate and mandatory.

---

## C-Class Checklist

| Check | Description |
|-------|------------|
| C2 | Gross exposure never exceeds 100% capital |
| C5 | Returns are multiplicative (`equity *= (1 + ret)`), not additive |
| C-exit | Missing exit price: use `settle_price_fallback()` — earlier-first same-day search. Log all gaps to `data_gaps.json` for review. If strategy uses a pre-built cache without DB connection, log gaps with `resolution: "carry_forward"` or `"flat_penalty"` |
| C-TC | TC from `shared/config.py`, proportional to turnover (not flat per day) |
| C-split | Split filters on BOTH sides via authoritative records |
| C-sizing | Position sizing from point-in-time info only, never from realized schedule |

---

## TC Cases

- **Case 1 (Nightly turnover):** `2*TC` per active day
- **Case 2 (Hold-through):** TC only on state change. `if prev != current` guard required.
- **Case 3 (Intraday):** `2*TC` per trade

---

## Details

See `reference/accounting-details.md` for full case descriptions, code patterns, and edge cases.
