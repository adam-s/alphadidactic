# The Things That Bite in Production

TimescaleDB works out of the box. I mean that genuinely — install the extension, convert a table to a hypertable, queries get faster. The documentation is excellent. The SQL interface is pure Postgres. But "works out of the box" and "works in production" are separated by a set of environment-level mistakes that bite hardest when TimescaleDB is involved. None of these are TimescaleDB bugs. All of them are things I fixed after the fact.

## The healthcheck that ate itself

My Docker Compose healthcheck was running `bun -e "import('./run.ts')"` every 30 seconds. That doesn't check health — it starts a full copy of the application. Database connections, background workers, event listeners, everything. Every 30 seconds. The copies overlap because the previous one hasn't finished initializing. Memory climbs. The container OOMs, Docker restarts it, and the cycle begins again.

The fix:

```yaml
healthcheck:
  test: ['CMD-SHELL', 'pg_isready -U volatio -d causal_market']
  interval: 10s
  timeout: 5s
  retries: 5
```

For non-database containers, `curl -f http://localhost:3000/health` works fine. The rule: a healthcheck must never import application code.

## CI doesn't need TimescaleDB

My CI environment was pulling `timescale/timescaledb:latest-pg16`. The tests don't create hypertables. They don't call `time_bucket()`. They test application logic against standard PostgreSQL tables. Plain `postgres:16` works identically — same SQL engine, same wire protocol, minus an extension the tests don't exercise.

This led to a broader decision: one compose file per environment, each honest about what it needs.

| Environment | Image | Port | Notes |
|-------------|-------|------|-------|
| Dev | timescale/timescaledb:latest-pg16 | 5432 | shm_size: 2gb, external volume |
| Prod | timescale/timescaledb:latest-pg16 | internal | restart: unless-stopped, S3 backup |
| Test | timescale/timescaledb:latest-pg16 | 5442, 5443 | Two instances, ephemeral |
| CI | postgres:16 | 5452 | No TimescaleDB needed |
| E2E | timescale/timescaledb:latest-pg16 | 5433 | ssl=off for Playwright |

Five compose files. Each one explicit about its image, ports, and volume strategy. A single parametrized compose file with environment toggles was clever and unmaintainable.

## The shared memory trap

The `shm_size` row in that table tells a story. PostgreSQL uses `/dev/shm` for hash joins and sorts. Docker defaults to 64MB. The first time I ran a GROUP BY across millions of hypertable rows, I got "No space left on device." Not a disk error — a shared memory error. I spent time convinced my volume was full before realizing the error was about `/dev/shm`, not `/var/lib/postgresql/data`.

```yaml
shm_size: '2gb'  # Prevent "No space left on device" errors on parallel queries
```

For scripts running heavy aggregation against billion-row tables, there's an additional workaround — disable parallel query workers per-session to avoid exhausting shared memory entirely:

```python
cur.execute("SET max_parallel_workers_per_gather = 0")
```

## Idempotent setup or bust

Every setup script must be safe to re-run. `create_hypertable` throws if the table is already a hypertable. `add_compression_policy` errors if the policy exists. The fix is `if_not_exists` everywhere and existence checks before adding policies:

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

SELECT create_hypertable('minute_bars', 'time',
  chunk_time_interval => INTERVAL '1 week',
  if_not_exists => TRUE,
  migrate_data => TRUE
);
```

The goal is a single script that takes the database from any state — fresh, partially initialized, fully set up — to the correct state. Run it on first deploy, run it again after a migration, run it in CI. Same result every time.

## Module-level side effects

Not TimescaleDB-specific, but it bites hardest in a monorepo with a shared database package. If importing the package opens a connection at load time, every file that transitively imports it needs `DATABASE_URL` set. Test files, CLI tools, scripts that only need type definitions — they all crash on import if the environment variable is missing. The Proxy-based lazy initialization from [Post 01](./01-the-two-layer-trick.md) solves this by deferring the connection to the first actual query.

## The database consolidation

The project started with two databases: one for application data (users, sessions, configuration) on port 5432, and one for market data (minute bars, options trades) on port 5433. The rationale was clean separation — the market database needed TimescaleDB features, the app database didn't.

This lasted about a month. Research queries and production queries hit the same data. Cross-database joins don't exist in PostgreSQL. Connection management doubled. Migrations ran against two targets. The consolidation into a single database touched 100 files. Every test passed afterward, and the operational overhead dropped significantly.

The lesson: don't split databases by use case. If they query the same data, they belong in the same database.

## Delete unreliable data

The `options_trades` table originally had a `direction` column — buy or sell. It was inferred from OPRA condition codes (209, 227, 232, etc.). The problem: OPRA condition codes describe *how* a trade was executed (electronic, auction, floor), not the buy/sell direction. Direction inference requires NBBO quote data, which wasn't available.

The column was producing garbage data that looked plausible. Someone would eventually query it and draw false conclusions. The entire column was removed — schema, ingest function, type definitions, all of it.

A wrong column is worse than a missing column. If the data can't be trusted, delete it.

## The full lifecycle

Compression and retention aren't separate features. They form a data lifecycle. Add compression but forget retention, and disk grows forever — just slower. Add retention but forget compression, and disk grows 10-20x faster than it should between the write window and the delete window.

```sql
-- minute_bars: keep forever, compress after 30 days
ALTER TABLE minute_bars SET (timescaledb.compress,
  timescaledb.compress_segmentby = 'symbol',
  timescaledb.compress_orderby = 'time DESC');
SELECT add_compression_policy('minute_bars', INTERVAL '30 days');

-- event_log: compress after 1 day, delete after 30 days
SELECT add_compression_policy('event_log', INTERVAL '1 day');
SELECT add_retention_policy('event_log', INTERVAL '30 days');

-- price_cache: rolling 3-day window
SELECT add_retention_policy('price_cache', INTERVAL '3 days');
```

Hot data is recent and writable. Warm data is compressed and queryable. Cold data gets deleted automatically. Three SQL statements per table give you a lifecycle that runs unattended.

None of these lessons are complicated. A healthcheck that doesn't fork-bomb your container. A CI config that uses the right Postgres image. Setup scripts that don't break when you run them twice. A connection that doesn't open at import time. A lifecycle from ingestion to deletion. They're all small things. But small things compound, and in production, compounding works in both directions.

---

*TimescaleDB + Drizzle series:*
1. [The Two-Layer Trick](./01-the-two-layer-trick.md)
2. [Choosing Chunk Intervals](./02-choosing-chunk-intervals.md)
3. [Compression as Survival](./03-compression-as-survival.md)
4. [Continuous Aggregates](./04-continuous-aggregates.md)
5. [Bulk Ingestion](./05-bulk-ingestion.md)
6. [Query Patterns That Matter](./06-query-patterns-that-matter.md)
7. [Drizzle Migration Traps](./07-drizzle-migration-traps.md)
8. **The Things That Bite in Production** *(you are here)*
