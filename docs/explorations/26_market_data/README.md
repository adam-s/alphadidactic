# Market Data: From Flat File to Hypertable

Massive distributes market data as gzipped CSV files — one file per day, organized into three flavors. Stock minute bars. Options minute aggregates. Options trades. Each has a different shape, a different scale, and a different set of traps waiting in the ingestion code.

This is what the pipeline looks like when you open the files and start loading.

## What's in a flat file

Every file follows the same naming convention: `YYYY-MM-DD.csv.gz`. The directory structure tells you what's inside:

```
stocks/minute_aggs/2025/04/2025-04-01.csv.gz
options/minute_aggs/2025/04/2025-04-01.csv.gz
options/trades/2025/04/2025-04-01.csv.gz
```

Open the stock minute bars file and the CSV looks straightforward:

```
ticker,volume,open,close,high,low,window_start,transactions
AAPL,1694,221.42,221.41,221.42,221.41,1743508800000000000,29
```

That `window_start` value — `1743494400000000000` — is 19 digits. Nanoseconds since epoch. JavaScript's `Number.MAX_SAFE_INTEGER` is 16 digits. Parse it as a regular number and you get silent precision loss. The timestamp will be off by seconds or minutes, and nothing will throw.

```ts
Number(1743494400000000000)  // 1743494400000000000 — looks fine
Number(1743514201297000000)  // 1743514201297000000 — also looks fine?
// But: 1743514201297000000 !== 1743514201297000064
// JavaScript silently rounds to the nearest representable float
```

The fix is BigInt:

```ts
const ms = Number(BigInt("1743514201297000000") / BigInt(1_000_000));
new Date(ms); // 2025-04-01T19:30:01.297Z — exact
```

Every timestamp in every file needs this conversion. The stock bars, the options aggs, the trade ticks — all nanoseconds, all 19 digits.

## Three shapes of market data

The three file types share a naming convention but diverge in structure and scale.

**Stock minute bars** are the simplest. One row per symbol per minute. AAPL on April 1st produces 734 rows — roughly one for each minute of the trading session including pre-market and after-hours. Three days of AAPL compresses to 44 KB.

**Options minute aggregates** use the same CSV columns as stock bars, but the `ticker` field is an OCC options ticker instead of a stock symbol:

```
O:AAPL250404C00160000,1,60.28,60.28,60.28,60.28,1743514200000000000,1
```

That ticker encodes four things: the underlying (`AAPL`), the expiration (`250404` = April 4, 2025), the type (`C` = call), and the strike (`00160000` = $160.00). Each option contract gets its own minute bars. AAPL has thousands of active contracts at any time — different strikes, different expirations, calls and puts. April 1st alone has 21,960 rows. Three days: 106,806 rows, 1.0 MB compressed.

**Options trades** are individual ticks. Every trade that crosses the tape:

```
ticker,conditions,correction,exchange,price,sip_timestamp,size
O:AAPL250516C00255000,209,0,319,0.54,1743514200097000000,5
```

The `conditions` field is an OPRA condition code (209 = electronic trade). The `correction` column is always 0 in practice. The `exchange` is a numeric OPRA exchange code. One contract, one price, one size, one timestamp.

AAPL generates roughly 85,000–275,000 options trades per day. Three days: 437,802 rows, 4.2 MB compressed. SPY generates closer to 900,000 trades per day. A full year of SPY options trades is 920 million rows. That number changes the architecture.

## The staging table pattern

The ingestion code for all three types follows the same structure:

1. Parse the gzipped CSV into typed rows
2. Create a temporary staging table
3. Batch-insert into staging (5,000-6,000 rows per batch)
4. Move from staging to the real table

For stock bars and options aggs, the move uses `ON CONFLICT DO NOTHING`:

```sql
INSERT INTO minute_bars (symbol, time, open, high, low, close, volume, vwap, transactions)
SELECT symbol, time, open, high, low, close, volume, vwap, transactions
FROM minute_bars_staging
ON CONFLICT (symbol, time) DO NOTHING
```

This makes re-imports idempotent. Load the same file twice and the second run inserts zero rows.

Options trades are different. There's no primary key — two trades can have the exact same timestamp, ticker, price, and size. They're different trades that happened at the same instant. So the insert is a plain `INSERT INTO ... SELECT FROM staging` with no conflict handling. The import script checks whether data already exists for the date before loading.

Why a staging table at all? Two reasons. First, PostgreSQL's parameter limit of 65,535 per query caps batch size. With 10 columns per row, that's ~6,500 rows maximum. The staging table lets you batch-insert in chunks, then move everything to the hypertable in one operation. Second, if the insert fails partway through, the staging table is a temp table — it vanishes at the end of the connection. No partial data left in the real table.

## The options_trades constraint

The `options_trades` table in production has over 1.3 billion rows across 1,016 TimescaleDB chunks. That number creates a hard constraint: you must query one day at a time. A multi-day query forces PostgreSQL to plan across hundreds of chunks simultaneously. The query planner locks each chunk it might touch, and with 1,016 chunks, a broad query exhausts `max_locks_per_transaction` before results start flowing.

This isn't a configuration problem you tune away. It shapes the entire data pipeline. Every extraction script, every backtest, every analysis tool processes dates in a loop:

```python
for date in trading_days:
    rows = query_options_trades(symbol, date)  # one day at a time
    process(rows)
```

The seed dataset included here is small enough that this constraint doesn't bite. But when you scale to SPY or QQQ, the day-at-a-time pattern isn't optional.

## Loading the seed data

The seed files live in `data/seed/` — 9 gzipped CSVs totaling ~5.3 MB:

```bash
cd packages/db

# Create tables and convert to TimescaleDB hypertables
pnpm run setup

# Import all seed files
pnpm run import:seed
```

The import discovers files by directory path (`stocks/minute_aggs/` vs `options/trades/`), sorts by date, and calls the appropriate ingest service for each. After loading:

```sql
SELECT 'minute_bars', count(*) FROM minute_bars
UNION ALL SELECT 'options_minute_aggs', count(*) FROM options_minute_aggs
UNION ALL SELECT 'options_trades', count(*) FROM options_trades;
```

```
 minute_bars         |   2,463
 options_minute_aggs | 106,806
 options_trades      | 437,802
```

547,071 rows of AAPL data. Enough to query against, enough to see the shapes, small enough to track in git.

## Scaling up

The seed data uses AAPL because it's the most widely recognized ticker and compresses to ~5 MB. For real analysis you'll want SPY, QQQ, or the full universe.

Massive provides flat file access through their S3-compatible endpoint. With an API key, the download is a single `aws s3 cp` per file:

```bash
aws s3 cp \
  s3://flatfiles/us_options_opra/trades_v1/2025/04/2025-04-01.csv.gz \
  options/trades/2025/04/2025-04-01.csv.gz \
  --endpoint-url https://files.massive.com
```

The files land in the same directory structure. The same import utilities load them. The difference is scale — a single day of SPY options trades is 50-70 MB compressed. A year of the full options universe is measured in terabytes. The staging table pattern, the day-at-a-time queries, the inline compression after each import — all of it exists because of that scale.
