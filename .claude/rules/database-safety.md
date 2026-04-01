---
paths:
  - "shared/**"
  - "**/build_dataset.py"
  - "**/verify_integrity.py"
---

# Database & System Safety

A large time-series database with billions of rows. All tables are partitioned by time (chunks) — queries without time bounds scan ALL chunks. Run `python -m shared.db_monitor profile` for current table sizes.

**Symbol density:** Not all symbols are equal. Profile before querying:
```bash
python -m shared.db_monitor density SYMBOL    # look up one symbol
python -m shared.db_monitor profile           # rebuild full density cache
```
Single-day time bounds are mandated for ALL queries regardless of density. Density tells you how expensive even a single-day query is.

---

## Mandatory Rules

### 1. Always set statement_timeout

```python
from shared.db_monitor import safe_connection

with safe_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(...)
```

Sets `statement_timeout = 30s` and guarantees cleanup. Pass `statement_timeout="120s"` for longer operations.

### 2. Always bound queries by the time column

Every hypertable query MUST have a WHERE clause on the time column. Without it, TimescaleDB scans ALL chunks (69-101 GB).

### 3. Respect density tiers

Check symbol density before querying: `shared/db_monitor.get_density(symbol)`. Never exceed `density.max_options_window_days` or `density.max_minute_bars_window_days`.

### 4. EXPLAIN before any new query pattern

Look for chunk exclusion in the plan. Red flag: "Seq Scan" with no chunk pruning.

### 5. Never retry a failed query

The query is wrong — fix it, don't re-run it. Check for leaked connections first.

---

## Common Mistakes

- **Unbounded aggregation:** `SELECT count(*)` without time bounds scans entire table. Use `pg_class.reltuples`.
- **Increasing timeout:** The query is wrong, not the timeout.
- **Retry on connection refused:** Check for leaked connections first. Kill zombies. Connect once.
- **Assuming equal cost:** Two symbols can differ by 1000x. Always profile first.

---

## System Health

Run `python -m shared.system_monitor` before every experiment. CRITICAL = stop and resolve.

For recovery procedures, monitoring commands, and nuclear reset, see `reference/db-recovery.md`.
