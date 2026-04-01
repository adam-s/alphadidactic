# Choosing Chunk Intervals

There's a configuration string you pass once when creating a hypertable. Something like `'7 days'` or `'1 month'`. You pass it, the table starts working, and you never think about it again. I left mine at the default for months. It turns out chunk interval is the single most impactful decision you make for a hypertable, and getting it wrong doesn't throw an error — it just makes everything subtly slower until one day you're staring at a query that should be fast and isn't.

## What a chunk actually is

When you call `create_hypertable()`, TimescaleDB takes your regular PostgreSQL table and turns it into a parent table with automatically managed child tables. Each child table is a chunk, and each chunk holds rows from a specific time range. If your chunk interval is one week, every week of data lives in its own physical table. When you query for "last week's market data," the planner looks at the WHERE clause, determines which chunks overlap, and only scans those. Everything else gets skipped. This is chunk exclusion, and it's the reason TimescaleDB is fast. A table with 374 million rows behaves like a table with a few million — as long as the chunks are sized right.

## Too many chunks

My `options_trades` table grew to over a billion rows across 1,016 chunks. At that point the query planner started hitting "out of shared memory" errors. Every chunk is a real PostgreSQL table, and the planner evaluates each one to decide if it's relevant. With a thousand chunks, planning time itself became a bottleneck. The queries weren't slow because of data volume — they were slow because the planner was doing too much work before the query even started.

## Too few chunks

The opposite problem: if your chunk interval is one year and you query for a single day, you're scanning the full year's data. Writes get slower too when you're inserting into a chunk that holds hundreds of millions of rows. The sweet spot depends on your write volume and query patterns.

## Three intervals, three reasons

After working with tables at different scales, I settled on three chunk intervals. Each one maps to a real table with a specific access pattern.

**One week** for high-volume data with queries spanning days to weeks. My `minute_bars` table (374M+ stock price bars) uses weekly chunks. Most queries ask about a specific symbol over the last few days or weeks — weekly chunks keep each partition around 27 million rows, which the planner handles efficiently. Compression, continuous aggregates, and retention all operate at the chunk level, so weekly granularity gives fine-grained control over each.

**One month** for low-volume summary data with queries spanning months or years. `daily_bars` (~220K rows per month) uses monthly chunks. Queries typically span months of daily data, and monthly chunks mean fewer than 30 chunks for a couple years of history. The planner handles that effortlessly.

**One day** for ephemeral data with short retention. `event_log` (application events) uses daily chunks with a 30-day retention policy. Millions of events per day, queries that mostly ask about "today" or "last few hours." Daily chunks keep each partition small enough that writes stay fast, and dropping old data is just dropping a table — the retention policy calls `drop_chunks` and whole days disappear instantly.

```typescript
const HYPERTABLES: HypertableConfig[] = [
  { table: 'minute_bars',  timeColumn: 'time',       chunkInterval: '1 week' },
  { table: 'daily_bars',   timeColumn: 'time',       chunkInterval: '1 month' },
  { table: 'event_log',    timeColumn: 'event_time',  chunkInterval: '1 day' },
];
```

## Time column types

TimescaleDB supports three types for the time column, and I use all three for different reasons.

`minute_bars` uses plain `timestamp` without timezone. The data arrives as UTC and gets stored as-is. Queries handle timezone conversion explicitly with `AT TIME ZONE` when they need Eastern time for market hours filtering. This works because the application controls the timezone interpretation.

`options_trades` uses `timestamptz` (timestamp with timezone). The options trade data comes from external feeds where timezone handling matters at the storage level — `timestamptz` stores UTC internally and converts on display based on the session timezone. Chunk boundaries stay consistent regardless of who's querying.

`event_log` uses `bigint` — millisecond epoch. Application events from JavaScript clients already speak in epoch time, and converting to timestamps just to satisfy convention would be pointless friction. TimescaleDB handles bigint time columns natively, including chunk exclusion and all the policy features.

```typescript
// Plain timestamp — timezone handling at query time
time: timestamp('time', { mode: 'date' }).notNull(),

// timestamptz — timezone handling at storage time
sipTimestamp: timestamp('sip_timestamp', { withTimezone: true, mode: 'date' }).notNull(),

// bigint epoch ms — no conversion needed from JS clients
eventTime: bigint('event_time', { mode: 'number' }).notNull(),
```

## The first thing to check

When a query feels slow, I check `num_chunks` before anything else:

```sql
SELECT hypertable_name, num_chunks, compression_enabled
FROM timescaledb_information.hypertables
WHERE hypertable_schema = 'public';
```

If `num_chunks` is in the thousands, that's usually the answer. Either the chunk interval is too small for the data volume, or old chunks aren't being compressed or dropped. Everything downstream — compression, continuous aggregates, retention, even the query planner — operates chunk by chunk. Get the chunk interval right first. Everything else follows from there.

---

*TimescaleDB + Drizzle series:*
1. [The Two-Layer Trick](./01-the-two-layer-trick.md)
2. **Choosing Chunk Intervals** *(you are here)*
3. [Compression as Survival](./03-compression-as-survival.md)
4. [Continuous Aggregates](./04-continuous-aggregates.md)
5. [Bulk Ingestion](./05-bulk-ingestion.md)
6. [Query Patterns That Matter](./06-query-patterns-that-matter.md)
7. [Drizzle Migration Traps](./07-drizzle-migration-traps.md)
8. [The Things That Bite in Production](./08-production-lessons.md)
