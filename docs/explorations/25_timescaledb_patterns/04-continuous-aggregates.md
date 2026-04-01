# Continuous Aggregates

This covers how continuous aggregates replace manual rollup jobs and cron-based summary tables. It assumes familiarity with SQL `GROUP BY`, materialized views, and the basics of TimescaleDB hypertables covered in the earlier posts.

## The problem

A `SELECT ... GROUP BY time_bucket('1 hour', time)` query over `minute_bars` is clean and correct. With ten thousand rows it returns in milliseconds. With 374 million rows it takes seconds. Every page load. Every user. A standard `MATERIALIZED VIEW` could precompute the result, but refreshing it means rebuilding everything from scratch — all 374 million rows, even if only the last hour changed.

A continuous aggregate is a materialized view that TimescaleDB refreshes incrementally. It only processes the chunks that changed since the last refresh. That distinction is the difference between a full table scan and a targeted update.

## Building an hourly rollup

The `hourly_bars` aggregate rolls minute-level stock data into hourly OHLCV bars:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_bars
WITH (timescaledb.continuous) AS
SELECT
  symbol,
  time_bucket('1 hour', time) AS bucket,
  first(open, time) AS open,
  max(high) AS high,
  min(low) AS low,
  last(close, time) AS close,
  sum(volume) AS volume,
  sum(vwap * volume) / NULLIF(sum(volume), 0) AS vwap,
  count(*) AS bar_count
FROM minute_bars
GROUP BY symbol, time_bucket('1 hour', time)
WITH NO DATA;
```

Two things to note. `first(open, time)` and `last(close, time)` are TimescaleDB-specific aggregation functions. `first(value, time)` returns the value at the earliest timestamp within the group. `last(value, time)` returns the value at the latest. Standard SQL can't do this without a subquery or window function. For OHLCV construction, `first(open, time)` gives the opening price of the hour and `last(close, time)` gives the closing price. These compose well: `min(low)` and `max(high)` work correctly across any grouping, and the VWAP calculation weights by volume — `sum(vwap * volume) / NULLIF(sum(volume), 0)` — so hours with more trading activity contribute proportionally.

`WITH NO DATA` creates the view definition without materializing anything. On a 374-million-row table, the initial backfill could take a long time — you don't want that inside a migration or a deploy. Instead, control the backfill explicitly:

```sql
CALL refresh_continuous_aggregate('hourly_bars', '2023-01-01', NOW());
```

Run it during a maintenance window or a background job. The view exists immediately; history fills in on your schedule.

## Keeping it fresh

The refresh policy controls how the aggregate stays up to date:

```sql
SELECT add_continuous_aggregate_policy('hourly_bars',
  start_offset => INTERVAL '1 month',
  end_offset => INTERVAL '2 hours',
  schedule_interval => INTERVAL '1 hour'
);
```

Three parameters, each doing something specific. `schedule_interval` is how often the refresh runs — every hour here. `start_offset` defines how far back the refresh window extends. One month means that late-arriving data (a backfill, a delayed feed) gets picked up on the next refresh. `end_offset` is the subtle one: it tells TimescaleDB to skip the most recent two hours. Why? Because data is still arriving. If you materialize the 2:00 PM bucket at 2:45 PM, you get a partial aggregate missing 15 minutes of data. By setting `end_offset` to two hours, you only materialize complete buckets. For real-time queries against the last two hours, hit the raw `minute_bars` hypertable directly — fast because you're scanning a tiny slice.

## Daily returns from minute data

The `spy_daily` aggregate derives daily open-to-close returns for SPY, filtered to regular market hours:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS spy_daily
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 day', time) AS trade_date,
  first(open, time) AS day_open,
  last(close, time) AS day_close,
  (last(close, time) - first(open, time)) / NULLIF(first(open, time), 0) * 100
    AS return_pct
FROM minute_bars
WHERE symbol = 'SPY'
  AND (
    (EXTRACT(HOUR FROM time) = 9 AND EXTRACT(MINUTE FROM time) >= 30)
    OR (EXTRACT(HOUR FROM time) >= 10 AND EXTRACT(HOUR FROM time) < 16)
  )
GROUP BY time_bucket('1 day', time)
WITH NO DATA;
```

This replaced a separate daily bars sync job. The insight was straightforward: daily and hourly bars should be derived from minute bars via continuous aggregates, not stored independently. One source of truth, multiple views at different granularities.

## Options research aggregates

The same pattern extends to options data. Six continuous aggregates built on `options_minute_aggs` and `options_trades`:

- `options_daily_volume` — daily put/call volume by underlying
- `options_hourly_volume` — hourly volume patterns
- `options_daily_blocks` — daily block trade aggregates (size >= 100 contracts)
- `options_daily_pc_ratio` — daily put/call ratio
- `options_hourly_blocks` — hourly block trade flow
- `options_daily_vwks` — daily volume-weighted strike price

The daily put/call ratio looks like:

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS options_daily_pc_ratio
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 day', time) AS trade_date,
  underlying,
  SUM(CASE WHEN option_type = 'put' THEN volume ELSE 0 END) AS put_volume,
  SUM(CASE WHEN option_type = 'call' THEN volume ELSE 0 END) AS call_volume,
  SUM(volume) AS total_volume
FROM options_minute_aggs
GROUP BY trade_date, underlying
WITH NO DATA;
```

Querying this is instant. Querying the raw `options_minute_aggs` table for the same result scans hundreds of millions of rows.

## Compression on aggregates

Continuous aggregates are themselves hypertables — they accumulate chunks and those chunks grow. `spy_put_minute_cagg`, a minute-level aggregate for SPY put options, grew to 12GB. `spy_call_minute_cagg` hit 10GB. `hourly_bars` reached 8.5GB. All before anyone thought to compress them.

The fix is the same ALTER command used on base tables:

```sql
ALTER MATERIALIZED VIEW hourly_bars SET (timescaledb.compress = true);
SELECT compress_chunk(c) FROM show_chunks('hourly_bars') c
  WHERE c < now() - INTERVAL '7 days';
```

## The cascade principle

Derive coarser time-series from finer ones. Don't maintain separate data pipelines for hourly and daily summaries. Define the relationship as a continuous aggregate, let the database materialize it incrementally. Minute rolls up to hourly. Hourly can roll up to daily. Each layer reads from the layer above it, not from raw data. The daily aggregate scans millions of pre-aggregated hourly rows, not hundreds of millions of raw minute bars.

One caution with cascading averages: when rolling up pre-aggregated data, you can't just `avg(avg_temp)`. That gives equal weight to every hourly bucket regardless of how many readings it contained. The correct approach is `sum(avg_value * count) / NULLIF(sum(count), 0)`, weighted by the original observation count. `min(min_value)` and `max(max_value)` compose correctly. `sum()` composes correctly. Averages need the weighting trick.

---

*TimescaleDB + Drizzle series:*
1. [The Two-Layer Trick](./01-the-two-layer-trick.md)
2. [Choosing Chunk Intervals](./02-choosing-chunk-intervals.md)
3. [Compression as Survival](./03-compression-as-survival.md)
4. **Continuous Aggregates** *(you are here)*
5. [Bulk Ingestion](./05-bulk-ingestion.md)
6. [Query Patterns That Matter](./06-query-patterns-that-matter.md)
7. [Drizzle Migration Traps](./07-drizzle-migration-traps.md)
8. [The Things That Bite in Production](./08-production-lessons.md)
