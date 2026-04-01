# flow_cache — Pre-Aggregated Options Flow Data

## Chain of Custody

This cache contains **derived data** pre-aggregated from the raw `options_trades` table (6.58B rows). It exists because querying options data on-the-fly for cross-sectional experiments across 50+ symbols takes 4-8 hours. The cache compresses this to seconds.

**CRITICAL:** This is derived data, not raw data. Any bug in the aggregation pipeline propagates silently into every downstream experiment. The cache must be treated with the same paranoia as any other data source:

1. The aggregation code (`create_flow.py` in deep-research) has its own temporal audit
2. Experiments using the cache must document which columns they use and verify a sample against raw data
3. The adversary can and should audit the cache builder
4. For final validation of any experiment showing positive results, the entire cache should be rebuilt from scratch through the shared infrastructure pipeline

## Source

- **Builder:** `deep-research/research/causal_signal_research/flow_cache/create_flow.py`
- **Raw table:** `options_trades` (6.58B rows, EXTREME density for many symbols)
- **Price source:** `minute_bars` (backward gap-fill via `merge_asof(direction="backward")`)
- **Build time:** 2-4 hours with 4 workers
- **Last built:** 2026-03-10

## Schema

Each row = one `(trade_date, symbol, size_bucket, dte_bucket)` tuple.
7 size buckets x 7 DTE buckets = up to 49 rows per symbol per day.

| Column | Type | Description |
|--------|------|-------------|
| trade_date | date | Trading date |
| symbol | str | Ticker |
| size_bucket | str | micro / small / medium / block / large / mega / ultra |
| dte_bucket | str | 0DTE / Next-Day / 2-3 DTE / Weekly / 1 Month / 3 Months / Long-term |
| put_extrinsic_mm | float | Put extrinsic (time value) flow in $M |
| call_extrinsic_mm | float | Call extrinsic (time value) flow in $M |
| net_extrinsic_mm | float | put - call extrinsic (positive = bearish) |
| put_total_mm | float | Put total premium flow in $M |
| call_total_mm | float | Call total premium flow in $M |
| net_total_mm | float | put - call total |
| put_intrinsic_mm | float | Put intrinsic (ITM) flow in $M |
| call_intrinsic_mm | float | Call intrinsic (ITM) flow in $M |
| net_intrinsic_mm | float | put - call intrinsic |
| put_itm_trades | int | Count of ITM put trades |
| call_itm_trades | int | Count of ITM call trades |
| put_trades | int | Total put trade count |
| call_trades | int | Total call trade count |

## Size Bucket Classification

| Bucket | Contract Size Range | Proxy For |
|--------|-------------------|-----------|
| micro | 1-9 | Retail |
| small | 10-49 | Small retail / small inst |
| medium | 50-99 | Mid-size |
| block | 100-249 | Institutional |
| large | 250-499 | Large institutional |
| mega | 500-999 | Very large institutional |
| ultra | 1000+ | Institutional block |

## Temporal Correctness Audit

| Property | Status | Evidence |
|----------|--------|----------|
| Per-day queries | YES | `generate_series` produces per-day sessions; `JOIN sessions` bounds each trade to its session |
| Timezone conversion | YES | `AT TIME ZONE 'America/New_York'` on session boundaries (open/cutoff) |
| Cutoff time | 15:30 ET | Trades after 15:30 ET excluded (configurable via `--cutoff-time`) |
| Gap-fill direction | Backward only | `merge_asof(direction="backward")` — never forward-fills |
| Same-day constraint | YES | Backward lookback falls back to first bar of the day, never crosses day boundaries |
| Intrinsic computation | At trade time | `max(S-K, 0)` for calls, `max(K-S, 0)` for puts, using underlying price at trade time |
| DISTINCT ON | NOT USED | Aggregation via `groupby().agg()`, not SQL DISTINCT ON |

## Known Limitations

1. **Date range ends 2026-03-02** — experiments extending beyond this date will have missing flow data
2. **Cutoff at 15:30 ET** — trades from 15:30-16:00 ET are excluded. Strategies needing late-session flow must use `OptionsTradesSource` directly for the 15:30-16:00 window
3. **No publication lag modeled** — flow data is point-in-time by construction (aggregated from trades that occurred during the session), but the aggregation itself runs after market close. For intraday strategies, verify that the flow data was available at the signal computation time
4. **Splits not adjusted in cache** — strikes and prices are as-reported. Intrinsic decomposition uses the live underlying price at trade time, which is correct for point-in-time analysis

## How to Use in Experiments

```python
import pandas as pd

# Load one symbol
spy_flow = pd.read_parquet("flow_cache/SPY_flow.parquet")

# Filter to institutional block trades, 0DTE
inst_0dte = spy_flow[
    (spy_flow["size_bucket"].isin(["block", "large", "mega", "ultra"])) &
    (spy_flow["dte_bucket"] == "0DTE")
]

# Aggregate across size buckets for a daily signal
daily = inst_0dte.groupby("trade_date").agg(
    call_premium=("call_total_mm", "sum"),
    put_premium=("put_total_mm", "sum"),
).reset_index()
daily["pc_ratio"] = daily["put_premium"] / (daily["put_premium"] + daily["call_premium"])
```

## Verification Requirements for Experiments Using This Cache

1. **Check 1 (cache vs raw):** For at least 5 dates, query `options_trades` directly via `OptionsTradesSource` and verify the aggregated values match the cache within tolerance
2. **Document which columns are used** in PRE_FLIGHT.md — the adversary will audit only those columns
3. **Document the cutoff time limitation** if the strategy uses late-session flow
4. **If the experiment shows positive results,** the adversary should rebuild the relevant columns from raw data for a sample of dates and compare against the cache
