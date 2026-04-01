# External Data with Publication Lag

Any data source with a publication delay (macro releases, earnings, regulatory filings) must model availability forward from the observation date, not backward from the decision date. Backward inference (`decision_date - lag → latest_available`) inverts the logic and produces off-by-one errors near holidays and weekends.

**Correct direction:**
```
available_at = observation_date + publication_lag
```
Then check: `available_at <= decision_time`.

**Use shared infrastructure** for all external data with publication lag. Raw SQL against external data tables bypasses the temporal availability chain. When the shared source provides an `available_at` timestamp, use it as the temporal gate — do not rely solely on metadata labels (e.g., "bmo"/"amc") which may be incorrect.

**Calendar gaps between trading days:** When N calendar days separate consecutive trading days, events released on intervening days may be available. Query all intermediate calendar dates, not just the previous trading day.

**Never write raw queries** against FRED/earnings/filings — use the shared source, or build one that models publication lag. Raw SQL is acceptable only as a panel loader passed to a shared source when: (1) not a hypertable, (2) date-bounded, (3) the source handles lag.

**Pre-aggregated caches** require a `MANIFEST.md` documenting build script, source table, aggregation logic, and temporal audit. Experiments must verify samples against raw data (Check 1). The adversary audits the cache builder code, not just the experiment. For final validation of positive results, the adversary should rebuild relevant columns from raw data for a sample of dates and compare against the cache. The cache is a performance optimization, not a trust bypass — same temporal correctness rules apply.

---

## DST Audit Procedure

Every experiment using time-of-day signals must verify DST handling.

**How to find transitions:** Compute dynamically with `dateutil.tz` — never hardcode. Select the nearest trading day on each side of each transition.

**Log format:**
```
DST Check [YYYY-MM-DD]: UTC bounds HH:MM:SS - HH:MM:SS (local HH:MM - HH:MM, zone)
```

**Verification:** Compare pre- and post-transition entries. UTC bounds must shift by the DST offset while local times stay the same.
