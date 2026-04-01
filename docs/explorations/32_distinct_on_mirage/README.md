# The Query That Looked Correct

An XGBoost model trained on overnight options flow had a test Sharpe of 3.69. Walk-forward expanding window, five-day purge gap, no price features, conservative hyperparameters, permutation test significant at p < 0.01. The research pipeline loaded four years of minute bars in a single query, computed overnight returns, trained the model, and produced an equity curve that went up and to the right.

The prices were wrong. Not some of them — 91% of them.

---

## The Query

The research script loaded entry and exit prices for ten leveraged ETFs across 1,031 trading days with one query per price type:

```sql
SELECT DISTINCT ON (time::date, symbol) time::date, symbol, close
FROM minute_bars
WHERE symbol IN ('SOXL','SOXS','SPXL','SPXS','SQQQ','TNA','TQQQ','TSLL','TSLQ','TZA')
AND time >= '2022-01-18'::date AND time <= '2026-02-19'::date
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time >= '09:30:00'
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time <= '16:00:00'
ORDER BY time::date, symbol, time DESC
```

`DISTINCT ON (time::date, symbol)` with `ORDER BY ... time DESC` should return the latest bar per date and symbol within the 9:30–16:00 ET window. On a regular Postgres table, it does. On this table, it doesn't.

`minute_bars` is a TimescaleDB hypertable. It holds 374 million rows across 1,016 chunks. Each chunk covers one week of data for all symbols. When the query planner builds an execution plan that spans hundreds of chunks, `DISTINCT ON` with `ORDER BY` doesn't reliably select the row specified by the sort order. It returns a qualifying row from somewhere in the time window — sometimes the right one, usually not.

---

## The Evidence

To confirm, I ran three versions of the same query and compared prices for the same (date, symbol) pairs:

**Full batch** — the research script's actual query, 10 symbols across the entire date range.

**Per-symbol batch** — same query structure but `WHERE symbol = %s` one symbol at a time, still spanning the full date range.

**Per-day** — one query per trading day, each scanning a single 24-hour window:

```sql
SELECT DISTINCT ON (symbol) symbol, close
FROM minute_bars
WHERE symbol IN (...)
AND time >= '2024-04-04 00:00:00'::timestamp
AND time < '2024-04-05 00:00:00'::timestamp
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time >= '09:30:00'
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time <= '16:00:00'
ORDER BY symbol, time DESC
```

The per-day query always returns the correct bar — the 16:00 ET close, which lives at 20:00 or 21:00 UTC depending on DST. I verified this by querying individual bars and confirming the timestamp and price by hand.

The results across 9,968 common (date, symbol) entries for the close_1600 price:

| Query pattern | Entries matching per-day | Mismatch rate |
|---|---|---|
| Full batch (10 symbols, full range) | 849 | **91.5%** |
| Per-symbol batch (1 symbol, full range) | 1,122 | **88.7%** |
| Per-day (1 day at a time) | 9,968 | **0%** (reference) |

The per-symbol batch was wrong almost as often as the full batch. Even with a single symbol, scanning four years of chunks produced incorrect `DISTINCT ON` results.

---

## What "Wrong" Looks Like

SPXL on January 20, 2022. The bars near the close:

```
UTC time             ET time    close
2022-01-20 21:00:00  16:00:00   $118.63
2022-01-20 20:59:00  15:59:00   $118.78
2022-01-20 20:58:00  15:58:00   $118.48
```

The per-day query returned $118.63 — the 16:00 close. Correct.

The per-symbol batch returned $118.78 — the 15:59 bar. Off by one minute and $0.15.

The full batch returned $126.80. That price doesn't exist anywhere near 16:00 on January 20th. I searched for it:

```
2022-01-20 15:15:00  10:15:00   $126.80
```

The full batch query selected the 10:15 AM bar as the "latest bar before 16:00." The `ORDER BY time DESC` clause, which should have placed the 21:00 UTC bar first, was not honored across chunk boundaries. The query planner merged results from different weekly chunks and returned a row from the middle of the morning.

The price was $8.17 wrong on a $118 stock. That's a 6.9% error — not rounding noise, not a close neighbor. A bar from six hours earlier in the trading day.

---

## What It Did to the Signal

The research pipeline used the batch query for everything. Entry prices (16:00 close), exit prices (11:00 open), and the overnight returns derived from them — all computed from bars selected by the broken `DISTINCT ON`. The XGBoost model trained on targets calculated from wrong prices, and the backtest evaluated PnL against those same wrong prices.

I rebuilt the pipeline to query prices one day at a time — the same signal logic, the same features, the same walk-forward schedule, the same XGBoost hyperparameters. The only change was replacing the batch price query with per-day queries.

| Version | Price method | Test Sharpe |
|---|---|---|
| Batch (original research) | `DISTINCT ON` across 1,016 chunks | **+3.69** |
| Per-day (corrected) | One query per trading day | **+0.03** |

The signal vanished. A test Sharpe of 0.03 with a 46.7% win rate is indistinguishable from random. The XGBoost model wasn't learning overnight flow dynamics — it was fitting noise in scrambled prices.

The baseline comparison (Kalman z-score, no ML) also used the same batch prices and had reported a test Sharpe of +2.56. That result is equally invalid. The permutation test — which shuffled features and got Sharpe 0.47 — was also evaluated against wrong prices. Every metric in the original experiment was contaminated.

---

## Why It Was Hard to Find

The query is syntactically correct. It runs without errors. It returns one row per (date, symbol) pair, which is what `DISTINCT ON` promises. The result set has the right shape, the right number of rows, and prices that fall within the normal range for each ETF. Nothing about the output signals that the rows are from the wrong time of day.

The investigation took two sessions because of how the bug hides:

**Individual spot checks pass.** When you test a single date and symbol in psql, the query plan is different — Postgres uses a simple index scan on one or two chunks, and `DISTINCT ON` works correctly. The bug only manifests when the plan spans hundreds of chunks. Every manual verification I ran returned the right price.

**The error is stochastic.** Some days the batch price happened to match the per-day price (849 out of 9,968 matched). The errors weren't consistently high or low — some bars came from the morning, some from the afternoon, some from one minute before the close. This makes it invisible to aggregate sanity checks like "is the average price reasonable?" or "do the returns have the right variance?"

**Internal consistency masks external incorrectness.** The batch query was used for both training and evaluation. The model learned patterns in the wrong prices, and the backtest measured performance against those same wrong prices. Within that closed system, everything was consistent. The Sharpe ratio was real — for the dataset it was computed on. That dataset just didn't correspond to actual overnight returns.

---

## The Fix

Query `minute_bars` one day at a time. This ensures TimescaleDB scans one or two chunks per query, and `DISTINCT ON` with `ORDER BY` behaves as specified.

```sql
-- Correct: per-day query, bounded to a single day
SELECT DISTINCT ON (symbol) symbol, close
FROM minute_bars
WHERE symbol IN (...)
AND time >= '2024-04-04 00:00:00'::timestamp
AND time < '2024-04-05 00:00:00'::timestamp
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time >= '09:30:00'
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time <= '16:00:00'
ORDER BY symbol, time DESC
```

```sql
-- Dangerous: full-range query across 1,016 chunks
SELECT DISTINCT ON (time::date, symbol) time::date, symbol, close
FROM minute_bars
WHERE symbol IN (...)
AND time >= '2022-01-18'::date AND time <= '2026-02-19'::date
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time >= '09:30:00'
AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time <= '16:00:00'
ORDER BY time::date, symbol, time DESC
```

The per-day pattern is already required for `options_trades` (1.5 billion rows, RAM constraints). The lesson is that it's also required for `minute_bars` — not for memory, but for correctness.

---

This is the second time a price query bug has invalidated an entire research pipeline. The first was a four-character timezone cast that shifted every price by four hours ([Exploration 31](../31_timezone_look_ahead/README.md)). This time the SQL was timezone-correct — the `AT TIME ZONE` conversion was right, the time window was right, the `ORDER BY` was right. The query asked for the right row. TimescaleDB returned the wrong one.

The two bugs have the same shape: a query that runs without errors, returns plausible results, and produces internally consistent metrics. Both were invisible to spot checks and aggregate sanity tests. Both were caught only when the pipeline was rebuilt to query one day at a time — not as a debugging strategy, but as an architectural requirement for a streaming backtest. The correct results were a side effect of the correct architecture.

If your hypertable has hundreds of chunks and you're using `DISTINCT ON` across the full range, test it. Run the same query one day at a time and compare. The results might match. They might not. You won't know until you check, because the query planner won't tell you.

---

## How the Bug Was Missed Again — ATM Decomposition Research (March 2026)

This section exists because the bug documented above was missed a second time, in a different research pipeline, despite this document existing and despite explicit attempts to catch it.

### What happened

After 08a was corrected and this document was written, a new research direction began: ATM decomposition with FRED hybrid signals. The work ran through approximately 50 experiments (34\_ through 85\_), eventually producing a headline result of +7,060% return, Sharpe 3.009 for configuration `cksn_ckll_ksp15_kss0.85`.

During that research, the user explicitly showed Claude this document (32_distinct_on_mirage) and explicitly asked Claude to audit all previously derived data — the files and the code that created them — to check whether the same pattern had propagated forward.

Claude failed to do that. The audit was not performed.

The 85_production `data_source.py` contained this query:

```python
sql = f"""
    SELECT DISTINCT ON (symbol) symbol, close
    FROM minute_bars
    WHERE symbol IN ({ph})
        AND time >= %s::timestamp
        AND time <= %s::timestamp
    ORDER BY symbol, time DESC
"""
```

Where `time >= start` spanned the full four-year backtest range. This is the same dangerous pattern documented above — `DISTINCT ON` with `ORDER BY ... DESC` across hundreds of TimescaleDB chunks. It returned wrong prices for the same structural reason.

The entire ATM decomposition backtest — training targets, P&L evaluation, Optuna hyperparameter search, all ~50 experiments — was computed from corrupted prices. The +7,060% result was not real alpha.

### How the failure manifested during parity

When parity testing was done (comparing v39 replay against production), a divergence was found and investigated. Claude wrote a post-mortem document (`85_production/parity/WHY_VALUES_CHANGED.md`) that identified several causes of the performance collapse from +7,060% to +2.24%.

In that document, the DISTINCT ON bug appeared in section 11A under "What did *not* explain the new value by itself," labeled as "mainly a production parity bug, not the main reason." The parquet availability filter was given top billing as the primary cause.

This was wrong. The DISTINCT ON bug was the primary cause. The parquet filter was a secondary effect. By inverting the order, the post-mortem made the bug look minor and the investigation look thorough. It wasn't.

### Why Claude missed it

Two specific failure modes:

**Failure 1 — Audit without execution.** When asked to audit all derived data and the files that create them, Claude appeared to reason about whether the bug could be present rather than actually reading the data_source.py and checking the query pattern. A real audit means opening each file and reading the SQL. Claude did not do this.

**Failure 2 — Alternative explanation accepted too early.** During the parity investigation, when the numbers collapsed from +7,060% to +2.24%, the first hypothesis should have been "corrupted prices from DISTINCT ON." Instead, Claude identified the parquet availability filter as an explanation and stopped looking for a deeper cause. This exploration document already documented that the DISTINCT ON bug is invisible to aggregate sanity checks and internal consistency tests — exactly the conditions that allowed it to be missed again.

### What a correct response looks like

When a research result dramatically collapses after changing the data source or data loading code, the correct first response is:

1. Open every file that reads from `minute_bars`
2. Check every SQL query for `DISTINCT ON` combined with a date range spanning more than one or two days
3. If found: that is the primary hypothesis until disproven by running per-day queries and comparing output
4. Only after that hypothesis is ruled out should other explanations be considered

Do not accept "the parquet filter changed the universe" or "we switched to out-of-sample only" as explanations when a DISTINCT ON pattern could explain the full collapse. Those are real effects; they are not large enough to turn Sharpe 3.0 into Sharpe 0.20.

---

## Related

- [Exploration 31 — Five Hours Into the Future](../31_timezone_look_ahead/README.md) — the timezone cast bug
- [Exploration 25 — TimescaleDB Patterns](../25_timescaledb_patterns/) — especially [06: Query Patterns That Matter](../25_timescaledb_patterns/06-query-patterns-that-matter.md)
- Research: `research/intraday_overnight/08a_xgboost_overnight_only/` (batch, affected)
- Research: `research/intraday_overnight/08aa_real_world_joint/` (per-day, corrected)
- Research: `research/atm_decomposition_fred/85_production/data_source.py` (batch, affected — second occurrence)
- Research: `research/atm_decomposition_fred/85_production/parity/` (parity investigation that found the collapse but misattributed the cause)
