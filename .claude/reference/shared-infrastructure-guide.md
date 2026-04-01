# Shared Infrastructure Quick-Start

This guide shows how to use the shared modules for every common data access pattern. **Use these instead of raw SQL.**

---

## Getting Minute-Bar Prices (CursorEngine)

```python
from datetime import date, time
from shared.cursor_engine import (
    CursorEngine, MinuteBarsSource, Checkpoint,
    ResolutionMode, build_schedule,
)
from shared.db_monitor import safe_connection

# 1. Define what prices you need (checkpoints)
close_checkpoint = Checkpoint(
    name="close",
    target_time_et=time(16, 0),
    mode=ResolutionMode.AT_OR_BEFORE,
    grace_minutes_before=390,  # R5: covers half-day closes (market closes at 13:00 ET)
)

# 2. Build an engine with those checkpoints
engine = CursorEngine(
    source=MinuteBarsSource(),
    checkpoints=[close_checkpoint],
)

# 3. Query one day at a time
with safe_connection() as conn:
    tape = engine.resolve_day(conn, date(2024, 6, 15), ["SPY", "QQQ"])
    spy_close = tape.get_price("close", "SPY")   # float or None
    qqq_close = tape.get_price("close", "QQQ")   # float or None
```

### Multiple checkpoints (e.g., intraday strategy)

```python
checkpoints = [
    Checkpoint("open",  time(9, 35), ResolutionMode.AT_OR_BEFORE, grace_minutes_before=5),
    Checkpoint("mid",   time(10, 30), ResolutionMode.AT_OR_BEFORE, grace_minutes_before=90),
    Checkpoint("close", time(15, 52), ResolutionMode.AT_OR_BEFORE, grace_minutes_before=390),  # R5: half-day
]
engine = CursorEngine(source=MinuteBarsSource(), checkpoints=checkpoints)

with safe_connection() as conn:
    for trade_day in trading_days:
        tape = engine.resolve_day(conn, trade_day, symbols)
        p_open  = tape.get_price("open", "SPY")
        p_mid   = tape.get_price("mid", "SPY")
        p_close = tape.get_price("close", "SPY")
```

### Per-experiment price cache

Each experiment builds and owns its own cache. No sharing — a bug in one cache cannot affect others.

```python
from shared.cursor_engine import build_price_cache, load_price_cache, CachedPhasedDay

# Build on first run, reuse on subsequent runs (including Optuna trials)
cache_path = OUT / "price_cache.parquet"
if not cache_path.exists():
    build_price_cache(engine, conn, trading_days, symbols, cache_path)
price_cache = load_price_cache(cache_path)

# Use CachedPhasedDay instead of PhasedDay — same interface
for today in trading_days:
    phased = CachedPhasedDay(price_cache, today, schedule)
    m = phased.resolve_up_to(clock_time(9, 35))
    p0935 = m.get("p0935", {})
```

The cache stores flat resolved prices (one row per date/symbol, one column per checkpoint). Prices are fully resolved — grace windows and half-day fallbacks already applied at build time. Delete `price_cache.parquet` to rebuild from DB.

**Check 6 and the cache:** For small universes (≤200 symbols), Check 6 should use raw DB via `engine.resolve_day()`. For large universes (900+ symbols), Check 6 may use the price cache — Check 1 already verifies cache == raw DB. The reason: with large universes, the ~45-minute raw-DB loop causes HMM regime models to converge differently due to floating-point nondeterminism, producing false Check 6 failures. The prices themselves match; only nondeterministic models (HMM) diverge.

### PhasedDay — temporal phase enforcement

`PhasedDay` is the core pattern for all experiments. It enforces that prices are resolved in strict temporal order — you cannot access 15:30 prices before processing 09:35.

```python
from shared.cursor_engine import PhasedDay

for today in trading_days:
    phased = PhasedDay(engine, conn, today, symbols, schedule, trading_days)

    # Phase 1: 09:35 — settle overnight, update accumulators
    m = phased.resolve_up_to(clock_time(9, 35))
    p0935 = m.get("p0935", {})

    # Phase 2: 15:30 — compute signal, build candidates
    aft = phased.resolve_up_to(clock_time(15, 30))
    p1530 = aft.get("p1530", {})

    # Phase 3: 16:00 — store close for next day
    cl = phased.resolve_up_to(clock_time(16, 0))
    p1600 = cl.get("p1600", {})
```

Each `resolve_up_to()` returns only checkpoints at or before the given time. Later checkpoints are physically inaccessible until their phase is reached.

### R5: Half-day close handling

**p1600 must use `grace_minutes_before=390`** to cover early-close days (13:00 ET on day before Thanksgiving, Christmas Eve, July 3). With `grace_minutes_before=5`, p1600 returns None on half-days — this leaves `prev_p1600` stale, contaminating the next day's EMA/accumulator inputs. All paradigm experiments use 390.

### Key rules

- One `resolve_day()` call per trading day — never query multiple days at once
- `get_price()` returns `None` if no bar found within the grace window
- CursorEngine handles dual timezone conversion internally — you never write `AT TIME ZONE`

### Settlement price fallback

When a checkpoint returns `None` at settlement time, use `settle_price_fallback()`:

```python
from shared.cursor_engine import settle_price_fallback

exit_price = p0935.get(sym)
if exit_price is None:
    exit_price, resolved_time, resolution = settle_price_fallback(
        engine, conn, sym, today, "09:35"
    )
    if exit_price is not None:
        data_gaps.append({"date": str(today), "symbol": sym,
            "target": "09:35", "resolved": resolved_time,
            "resolution": resolution, "price": exit_price})
```

**Why earlier-first works:** The data collector typically skips bars when price is unchanged. A missing bar at 09:35 usually means the price didn't change since the last recorded bar — that earlier bar IS the current price. Search order: (1) most recent bar before target, (2) earliest bar after target, (3) None.

**Limitations — always case by case:**

- This applies to equities and ETFs where a missing bar = unchanged price
- Does NOT apply to options (which expire worthless — last price before expiry ≠ current value)
- Multi-day gaps may indicate real data issues, not unchanged prices
- All fallback resolutions are logged to `output/data_gaps.json` and must be reviewed

### data_gaps.json schema

Every experiment maintains a `data_gaps` list, written to `output/data_gaps.json`:

```python
data_gaps.append({
    "date": str(today),      # trading date
    "symbol": sym,            # instrument
    "target": "09:35",        # intended checkpoint time
    "resolved": "09:33",      # actual time used (or None)
    "resolution": "same_day_carry",  # "same_day_carry" | "same_day_forward" | "no_price"
    "price": 150.25,          # resolved price (or omitted if no_price)
})
```

Zero gaps is normal for liquid instruments. Non-zero gaps require review in TEMPORAL_PROOF.md.

### trade_log.json (optional)

Experiments can log individual trades to `output/trade_log.json` via `save_results(... trade_log=trade_log)`. Append one dict per settlement, using the actual checkpoint names and times from the experiment's schedule:

```python
trade_log.append({
    "entry_date": str(entry_date),
    "entry_checkpoint": entry_checkpoint_name,  # e.g. "p1530", "p1600"
    "settle_date": str(settle_date),
    "settle_checkpoint": settle_checkpoint_name,  # e.g. "p0935", "p1600"
    "symbol": sym,
    "entry_price": entry_price,
    "exit_price": exit_price,
    "return": net_return,  # after TC and split filter
})
```

For multi-position strategies, one entry per symbol per settlement. The trade log makes Check 4 trivial — look up the trade instead of replaying the strategy.

---

## Getting FRED/Macro Data (FredLatestSource)

```python
from datetime import date
from shared.temporal_sources import FredLatestSource, FredLatestRequest
from shared.db_monitor import safe_connection

fred = FredLatestSource()

with safe_connection() as conn:
    snapshot = fred.fetch(
        FredLatestRequest(as_of_trade_date=date(2024, 6, 15)),
        conn=conn,
    )

    # snapshot.payload is a dict: series_id → FredObservation or None
    t10y2y = snapshot.payload.get("T10Y2Y")
    if t10y2y is not None:
        value = t10y2y.value                    # float (e.g., 0.42)
        obs_date = t10y2y.observation_date      # date (when FRED observed it)
        avail = t10y2y.available_at_utc         # datetime (when it was published)

    # Available series: T10Y2Y, T10Y3M, DFF, BAMLH0A0HYM2, VIXCLS, etc.
    # See shared/config.py PUBLICATION_LAGS for the full list
```

### Key rules
- FredLatestSource handles publication lag automatically (typically T+1 business day)
- `as_of_trade_date` means "what was available by market open on this date"
- Always check for `None` — the series may not have data for that date
- Never query FRED tables directly with raw SQL

---

## Getting Options Flow Data (OptionsTradesSource)

```python
from datetime import date, time
from shared.temporal_sources import OptionsTradesSource, OptionsWindowRequest
from shared.db_monitor import safe_connection

opts = OptionsTradesSource()

with safe_connection() as conn:
    snapshot = opts.fetch(
        OptionsWindowRequest(
            trade_date=date(2024, 6, 15),
            underlying="SPY",
            start_et=time(15, 0),      # 3:00 PM ET
            end_et=time(16, 0),        # 4:00 PM ET
        ),
        conn=conn,
    )

    # snapshot.payload is a pd.DataFrame with columns:
    #   sip_timestamp, symbol, underlying, size, price, condition_flags, ...
    df = snapshot.payload
    # Filter by size for institutional trades:
    inst = df[df["size"] >= 100]
    calls = inst[inst["condition_flags"].str.contains("C", na=False)]
    puts  = inst[inst["condition_flags"].str.contains("P", na=False)]
```

### For aggregated flow (SimpleFlowSource)

```python
from shared.temporal_sources import SimpleFlowSource, FlowWindowRequest

flow = SimpleFlowSource()

with safe_connection() as conn:
    snapshot = flow.fetch(
        FlowWindowRequest(
            trade_date=date(2024, 6, 15),
            underlying="SPY",
            start_et=time(15, 0),
            end_et=time(16, 0),
        ),
        conn=conn,
    )

    # snapshot.payload is a FlowWindowSummary with:
    #   .total_call_premium, .total_put_premium, .total_call_volume, etc.
    summary = snapshot.payload
    pc_ratio = summary.total_put_premium / max(summary.total_call_premium, 1)
```

### For cross-sectional experiments (many symbols): use flow_cache

Querying `options_trades` for 50+ symbols × 1000+ days takes hours. The pre-aggregated `flow_cache/` has options flow data for 917 symbols, pre-computed with intrinsic/extrinsic decomposition across size and DTE buckets.

```python
import pandas as pd

# Load pre-aggregated flow for one symbol
flow = pd.read_parquet("flow_cache/AAPL_flow.parquet")

# Filter to institutional (block+) trades
inst = flow[flow["size_bucket"].isin(["block", "large", "mega", "ultra"])]

# Daily aggregate
daily = inst.groupby("trade_date").agg(
    call_prem=("call_total_mm", "sum"),
    put_prem=("put_total_mm", "sum"),
).reset_index()
```

**Chain of custody:** The cache is derived data. Read `flow_cache/MANIFEST.md` for the temporal audit and `flow_cache/create_flow.py` for the builder code. Your experiment must verify a sample against raw data (Check 1). See `rules/temporal-correctness.md` § pre-aggregated caches.

**When to use flow_cache vs OptionsTradesSource:**
- **flow_cache:** Cross-sectional experiments across many symbols, daily granularity, OK with 15:30 ET cutoff
- **OptionsTradesSource:** Single-symbol experiments, custom time windows (e.g., 15:55-16:00 ET), need raw trade-level data

### Key rules
- Always check symbol density before querying: `shared.db_monitor.get_density(symbol)`
- SPY options are EXTREME density — always single-day queries
- OptionsTradesSource handles timezone conversion internally

---

## Getting Earnings Dates (EarningsReleasesSource)

```python
from datetime import date
from shared.temporal_sources import EarningsReleasesSource
from shared.db_monitor import safe_connection

earnings = EarningsReleasesSource()

with safe_connection() as conn:
    snapshot = earnings.fetch_for_symbol(
        conn=conn,
        symbol="AAPL",
        trade_date=date(2024, 6, 15),
    )

    # Returns earnings events near this date with available_at timestamps
    # The source handles publication lag — only returns events
    # whose dates were known by the given trade_date
```

### Key rules
- Earnings dates are forward-looking — a company announces its date weeks in advance
- Use `available_at` from the source, not just the earnings date itself
- Never query `earnings_releases` directly with raw SQL

---

## Getting the Symbol Universe

```python
from shared.research_core import get_symbols
symbols = get_symbols()  # 153 symbols, sorted
```

`get_symbols()` checks caches in order: `shared/cache/symbol_universe.json` → `shared/cache/intraday_prices.parquet` → DB query. The JSON cache contains symbols with ≥90% of SPY's trading day coverage. For cross-sectional experiments (base overnight, flow gapfill), filter out index ETFs: `EXCLUDE = {"SPY", "QQQ", "VXX"}`.

---

## Getting Trading Days

```python
from shared.live_loop import select_trading_days
from shared.db_monitor import safe_connection

with safe_connection() as conn:
    trading_days = select_trading_days(
        conn=conn,
        anchor_symbol="SPY",
        start_date=date(2022, 1, 18),
        end_date=date(2026, 2, 28),
    )
# Returns sorted list of dates where SPY had trading activity
```

**Never scan the full hypertable** to discover trading days. Use this function.

---

## Common Mistakes

| Wrong | Right | Why |
|-------|-------|-----|
| `cur.execute("SELECT ... FROM minute_bars WHERE ...")` | `engine.resolve_day(conn, trade_date, symbols)` | Raw SQL bypasses timezone handling |
| `pd.read_sql("SELECT ... FROM fred_observations ...")` | `FredLatestSource().fetch(...)` | Raw SQL bypasses publication lag |
| `cur.execute("SELECT DISTINCT date FROM minute_bars WHERE time >= ... AND time < ...")` | `select_trading_days(conn, ...)` | Multi-year scan on hypertable |
| `SPLIT_THRESHOLD` as only split protection | `build_default_split_ledger()` + magnitude fallback | Magnitude filters miss forward splits on leveraged ETFs |
