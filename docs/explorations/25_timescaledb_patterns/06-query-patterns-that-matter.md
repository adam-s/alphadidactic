# Query Patterns That Matter

Queries against a TimescaleDB hypertable look like normal SQL. They are normal SQL — until you change a WHERE clause from a direct timestamp comparison to an `EXTRACT()` call and the query goes from scanning two chunks to scanning every chunk in the table. These are the patterns that make the difference between fast and slow, drawn from a system with 374 million minute bars and 1.3 billion options trades.

## Chunk exclusion

The single most important performance lever. TimescaleDB partitions data into time-based chunks, and the query planner skips chunks that can't contain matching rows — but only if it can reason about the time bounds statically.

```sql
-- Enables chunk exclusion (scans 2 of 52 weekly chunks)
WHERE time >= '2024-03-01'::timestamptz AND time < '2024-03-15'::timestamptz

-- Defeats chunk exclusion (scans all 52 chunks)
WHERE EXTRACT(YEAR FROM time) = 2024 AND EXTRACT(MONTH FROM time) = 3
```

Both return the same rows. The second version wraps the time column in a function, so the planner can't determine which chunks to skip and falls back to scanning everything. The difference can be 25x — a two-week query scanning two chunks versus scanning an entire year.

**Gotcha**: Any function applied to the time column defeats exclusion. `date_trunc('day', time)` in a WHERE clause, casting to date, even timezone conversion in the wrong position. Always filter with direct comparisons on the raw time column, then do transformations in SELECT or GROUP BY.

## DISTINCT ON for latest-row lookups

"Give me the most recent price for each symbol." This query runs on every market data page load. The standard approaches — correlated subqueries, `ROW_NUMBER() OVER`, self-joins — are all slower than PostgreSQL's `DISTINCT ON`:

```sql
-- Latest price per symbol (single-day backward gap-fill)
SELECT DISTINCT ON (symbol) symbol, close
FROM minute_bars
WHERE symbol = ANY($1)
  AND time >= $2::timestamp AND time <= $3::timestamp
ORDER BY symbol, time DESC
```

The `ORDER BY` must start with the `DISTINCT ON` columns, followed by the column that picks the "best" row — `time DESC` for most recent. The time-range WHERE clause ensures chunk exclusion still works. Without it, DISTINCT ON scans the entire hypertable.

For multi-day price history — one closing price per symbol per day:

```sql
SELECT DISTINCT ON (symbol, time::date)
    symbol, time::date as trade_date, close as price
FROM minute_bars
WHERE symbol = ANY($1)
  AND time >= $2::timestamp AND time <= $3::timestamp
  AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
      >= '09:30:00'::time
  AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
      <= '15:30:00'::time
ORDER BY symbol, time::date, time DESC
```

This picks the last bar at or before 3:30 PM Eastern for each symbol on each day. The double `AT TIME ZONE` is explained below.

## time_bucket()

Standard SQL gives you `date_trunc`, which only works at fixed boundaries (hour, day, month). TimescaleDB's `time_bucket()` aggregates at arbitrary intervals — 15 minutes, 6 hours, 3 days:

```sql
SELECT
  symbol,
  time_bucket('1 day', time) as day,
  first(open, time) as open,
  last(close, time) as close
FROM minute_bars
WHERE symbol = $1 AND time >= $2 AND time < $3
GROUP BY symbol, time_bucket('1 day', time)
ORDER BY day
```

`first(open, time)` and `last(close, time)` are TimescaleDB-specific ordered aggregation functions. `first()` returns the value at the earliest timestamp in the group; `last()` returns the value at the latest. Essential for OHLC construction where you need the opening bar's open price and the closing bar's close price.

## time_bucket_gapfill() + locf()

When a stock is illiquid, some time buckets have no data. Charts draw misleading lines across the gaps. `time_bucket_gapfill()` creates empty buckets for missing intervals, and `locf()` (Last Observation Carried Forward) fills them with the last known value:

```python
SELECT
    symbol,
    time_bucket_gapfill('15 minutes', time) as interval_time,
    locf(last(close, time)) as close,
    locf(last(open, time)) as open,
    locf(last(high, time)) as high,
    locf(last(low, time)) as low,
    sum(volume) as volume
FROM minute_bars
WHERE symbol IN ('ILLIQUID_TICKER')
  AND time >= '2024-01-15' AND time <= '2024-01-15 21:00:00'
  AND time::time >= '09:30:00' AND time::time <= '16:00:00'
GROUP BY symbol, time_bucket_gapfill('15 minutes', time)
```

`locf()` wraps `last()` — if the bucket has data, `last(close, time)` returns the closing price; if empty, `locf()` carries forward the previous bucket's value. There's a sibling function `interpolate()` for linear fill between known points. Use `locf()` for prices (the last known price is still the current price) and `interpolate()` for measurements where smooth transitions between known points are physically plausible.

## Timezone-aware aggregation

`minute_bars.time` stores UTC. Market hours are Eastern. A "trading day" in America/New_York doesn't start at midnight UTC — it starts at 05:00 UTC in winter (EST) and 04:00 UTC in summer (EDT). The production query uses a double `AT TIME ZONE` conversion:

```sql
((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
```

The first `AT TIME ZONE 'UTC'` declares the stored value is in UTC. The second `AT TIME ZONE 'America/New_York'` converts to Eastern. This handles DST transitions automatically.

**JavaScript gotcha**: `new Date('2024-03-01')` parses as UTC midnight, which is still February 29 in any timezone west of UTC. Date filters end up off by one day. The fix: always parse at noon — `new Date('2024-03-01T12:00:00')`. Noon is far enough from any midnight boundary that timezone offsets can't push you into the wrong date.

## Timestamp precision

External data feeds send epoch timestamps without documenting the precision. Count the digits:

| Digits | Precision | Example |
|--------|-----------|---------|
| 10 | Seconds | 1709312400 |
| 13 | Milliseconds | 1709312400000 |
| 16 | Microseconds | 1709312400000000 |
| 19 | Nanoseconds | 1709312400000000000 |

PostgreSQL's `timestamptz` stores microsecond precision. Nanosecond timestamps (19 digits from Polygon flat files) need `/ 1000` and require BigInt to avoid floating-point truncation. Millisecond timestamps need `* 1000`. Feeding raw milliseconds into `to_timestamp()` (which expects seconds) produces dates thousands of years in the future.

## Disabling parallel queries on large tables

PostgreSQL parallelizes queries across workers that each allocate shared memory. On a billion-row hypertable with 1,016 chunks, the parallel workers can exhaust `/dev/shm` and crash with "out of shared memory." The workaround for heavy aggregation scripts:

```python
# Prevent parallel query workers to avoid shared memory exhaustion
cur.execute("SET max_parallel_workers_per_gather = 0")
```

This is a per-session setting. Production queries don't need it — they use bounded time ranges that scan a handful of chunks. It's the unbounded analytical queries during data processing scripts that hit the wall.

---

*TimescaleDB + Drizzle series:*
1. [The Two-Layer Trick](./01-the-two-layer-trick.md)
2. [Choosing Chunk Intervals](./02-choosing-chunk-intervals.md)
3. [Compression as Survival](./03-compression-as-survival.md)
4. [Continuous Aggregates](./04-continuous-aggregates.md)
5. [Bulk Ingestion](./05-bulk-ingestion.md)
6. **Query Patterns That Matter** *(you are here)*
7. [Drizzle Migration Traps](./07-drizzle-migration-traps.md)
8. [The Things That Bite in Production](./08-production-lessons.md)
