# Bulk Ingestion: When INSERT Hits a Wall

The options data import went through three stages. Each one was faster than the last, and each one required throwing away the approach that came before it. The end result — 1.3 billion rows loaded and compressed — works well. Getting there involved hitting two walls I didn't see coming.

## Stage one: parameterized INSERT

The first importer used standard parameterized INSERT batches. Clean TypeScript, Drizzle ORM, nice progress bar. It hit a ceiling almost immediately.

PostgreSQL has a hard limit of 65,535 parameters per query. That's a protocol-level constraint — 16-bit unsigned integer for the parameter count. You can't configure it away. The `options_trades` table has 10 columns. That caps each batch at 6,553 rows. Throughput: roughly 6,000 rows per second, give or take. At that rate, importing 1.3 billion rows would take weeks.

I tried smaller batches, bigger batches, disabling indexes during import, wrapping things in transactions. Nothing moved the needle more than 10-15%. The bottleneck isn't the code or the network — it's the query planner parsing thousands of parameters, building an execution plan, and inserting rows one logical operation at a time.

## Stage two: COPY protocol

PostgreSQL has a second ingestion path called COPY that bypasses parameter binding entirely. Instead of sending structured query parameters, you stream raw tab-delimited data directly into the table. The server reads it in a tight loop — no query plan, no parameter marshaling, no per-row overhead.

Throughput jumped from 6K to 16K rows per second on a single connection. The actual implementation uses a staging table pattern: COPY into a temp table, then INSERT INTO the main hypertable from staging. This avoids some edge cases with COPY directly into partitioned tables.

```typescript
// Create temp staging table
await sql`CREATE TEMP TABLE IF NOT EXISTS options_trades_copy_staging (
  sip_timestamp timestamptz NOT NULL,
  ticker text NOT NULL,
  underlying text NOT NULL,
  expiration date NOT NULL,
  option_type text NOT NULL,
  strike real NOT NULL,
  price real NOT NULL,
  size integer NOT NULL,
  exchange smallint,
  conditions text
)`;

// Stream data via COPY FROM STDIN
const copyResult = await sql`
  COPY options_trades_copy_staging (
    sip_timestamp, ticker, underlying, expiration, option_type,
    strike, price, size, exchange, conditions
  ) FROM STDIN
`.writable();

// Write in 10MB chunks to control memory
for (let i = 0; i < copyData.length; i += CHUNK_SIZE) {
  copyResult.write(copyData.slice(i, i + CHUNK_SIZE));
}
```

One important detail: COPY ties up the connection for the entire stream. You can't multiplex queries on a connection that's mid-COPY. Each worker needs its own dedicated connection, not one borrowed from the application pool. A pooled connection can get reclaimed mid-stream, and when that happens during COPY you get a cryptic "unexpected message during COPY" error.

```typescript
// Dedicated connection for bulk operations — not from the pool
export function createRawConnection(): ReturnType<typeof postgres> {
  return postgres(getDbUrl(), { max: 1 });
}
```

## Stage three: parallel COPY with inline compression

Single-connection COPY was 3x faster, but the data was split into monthly files anyway. Four workers pulling from a shared queue, each with its own dedicated connection:

```typescript
const WORKER_COUNT = 4;
const queue = [...files];

await Promise.all(
  Array.from({ length: WORKER_COUNT }, async (_, workerId) => {
    const conn = await createRawConnection();
    try {
      while (queue.length > 0) {
        const file = queue.shift()!;
        await importFile(conn, file);
        await compressRecentChunks(conn, 'options_trades');
      }
    } finally {
      await conn.end();
    }
  })
);
```

Four workers hit roughly 60 million rows per hour. I tried eight — throughput barely moved. At that point the disk I/O on the Postgres side was saturated.

The `compressRecentChunks` call after each file is essential. Without it, four workers streaming at full speed fill the disk in minutes. The auto compression policy runs on a schedule that can't keep up with a backfill. Compressing after each file keeps disk usage flat.

## The nanosecond surprise

The options data came from Polygon flat files. The timestamp field was 19 digits. Not milliseconds (13 digits). Not microseconds (16 digits). Nanoseconds. The initial parser treated them as milliseconds and produced timestamps fifty thousand years in the future. Every other column looked correct — just the time was wrong.

The fix required BigInt for accurate conversion:

```typescript
// CRITICAL: Polygon timestamps are NANOSECONDS (19 digits)
const timestampMs = Number(BigInt(rawTimestamp) / BigInt(1_000_000));
```

Counting digits is the simplest sanity check: 10 = seconds, 13 = milliseconds, 16 = microseconds, 19 = nanoseconds.

## The "no primary key" decision

`options_trades` has no primary key. Options trades can have identical timestamps — multiple trades at the exact same nanosecond are normal for heavily traded contracts. A composite key on `(ticker, sip_timestamp)` would reject valid data. Deduplication happens at the application level if needed, not at the storage level.

## Querying a billion rows

Once the data was loaded, a second wall appeared. A GROUP BY across the full 1.3-billion-row hypertable tried to materialize everything in memory. PostgreSQL builds hash tables for aggregation, and those need RAM. A simple "aggregate by underlying" query blew past `work_mem`, spilled to disk, then OOM'd the PostgreSQL process.

The pattern that works: iterate over trading days in application code, run a bounded query per day, accumulate results:

```python
# options_trades MUST be queried one day at a time
CHUNK_DAYS = 1

current_start = min_date
while current_start <= max_date:
    current_end = current_start + timedelta(days=CHUNK_DAYS - 1)
    daily_rows = aggregate_daily_flow(conn, symbol, current_start, current_end)
    accumulate(daily_rows)
    current_start = current_end + timedelta(days=1)
```

Each day-query hits a small number of chunks, uses bounded memory, and finishes in milliseconds. The full loop over a year of trading days takes seconds. The naive single query never finishes.

It feels wrong — like you're doing the database's job. But with billion-row hypertables, it's the only approach that stays within memory bounds.

---

*TimescaleDB + Drizzle series:*
1. [The Two-Layer Trick](./01-the-two-layer-trick.md)
2. [Choosing Chunk Intervals](./02-choosing-chunk-intervals.md)
3. [Compression as Survival](./03-compression-as-survival.md)
4. [Continuous Aggregates](./04-continuous-aggregates.md)
5. **Bulk Ingestion** *(you are here)*
6. [Query Patterns That Matter](./06-query-patterns-that-matter.md)
7. [Drizzle Migration Traps](./07-drizzle-migration-traps.md)
8. [The Things That Bite in Production](./08-production-lessons.md)
