#!/usr/bin/env python
"""
flow_cache/create_flow.py — Universal options flow cache
=========================================================

Generates per-symbol flow parquets with the RICHEST decomposition available:
  - Intrinsic flow  (ITM premium — directional conviction)
  - Extrinsic flow  (time value — sentiment/hedging)
  - Total premium   (useful for volume-weighted analysis)
  - ITM trade counts (institutional detection — large ITM puts = hedges)
  - Put/Call split for all three value types
  - 7 size buckets  (micro → ultra — retail vs institutional proxy)
  - 7 DTE buckets   (0DTE → Long-term — speculation vs hedging proxy)

Each row in the output = one (trade_date, symbol, size_bucket, dte_bucket) tuple
with 17 aggregate columns. With 7×7 = 49 bucket combos, that's up to 49 rows/day/symbol
giving 294 potential features per stock per day (49 × 3 value types × 2 sides).

Output columns per row:
  trade_date, symbol, size_bucket, dte_bucket,
  put_extrinsic_mm, call_extrinsic_mm, net_extrinsic_mm,
  put_total_mm, call_total_mm, net_total_mm,
  put_intrinsic_mm, call_intrinsic_mm, net_intrinsic_mm,
  put_itm_trades, call_itm_trades, put_trades, call_trades

Code lineage: Based on intrinsic_flow/02_dia_constituents/create_flow.py
(audited clean: per-day queries, AT TIME ZONE, backward-only gap-fill,
no vectorized leaks, no DISTINCT ON).

Usage:
    python create_flow.py                          # all symbols from symbols.txt
    python create_flow.py --symbols AAPL NVDA      # specific symbols
    python create_flow.py --workers 6              # more parallelism
    python create_flow.py --skip-existing          # resume after interruption
    python create_flow.py --combine-only           # re-combine existing parquets
    python create_flow.py --start-date 2024-01-01  # custom date range
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from tqdm import tqdm

# ── paths ─────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from utils import (  # noqa: E402
    get_db_connection,
    log_splits_in_range,
    find_flow_files,
    load_flow_files_with_progress,
)

# ── defaults ─────────────────────────────────────────────────────────────────
DEFAULT_START_DATE = "2022-01-18"
DEFAULT_END_DATE   = "2026-03-10"
DEFAULT_WORKERS    = 4
CUTOFF_TIME        = "15:30"
FLUSH_DAYS         = 200


def load_symbols(symbols_file: Path) -> list[str]:
    """Load symbols from text file, ignoring comments and blank lines."""
    symbols = []
    with open(symbols_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                symbols.append(line)
    return symbols


# ── SQL ───────────────────────────────────────────────────────────────────────
# Identical to 02_dia_constituents — audited clean.
# Per-day queries, AT TIME ZONE, weekend filter, proper DTE bucketing.

TRADES_QUERY = """
    WITH sessions AS (
        SELECT
            d::date AS trade_date,
            (d::timestamp + time '09:30') AT TIME ZONE 'America/New_York' AS open_ts,
            (d::timestamp + %(cutoff_time)s::time) AT TIME ZONE 'America/New_York' AS cutoff_ts
        FROM generate_series(
            %(start_date)s::date,
            (%(end_date)s::date - interval '1 day')::date,
            interval '1 day'
        ) AS d
        WHERE EXTRACT(DOW FROM d) BETWEEN 1 AND 5
    )
    SELECT
        t.sip_timestamp,
        s.trade_date,
        t.option_type,
        t.size,
        t.expiration,
        t.strike,
        t.price,
        CASE
            WHEN t.size >= 1000 THEN 'ultra'
            WHEN t.size >= 500  THEN 'mega'
            WHEN t.size >= 250  THEN 'large'
            WHEN t.size >= 100  THEN 'block'
            WHEN t.size >= 50   THEN 'medium'
            WHEN t.size >= 10   THEN 'small'
            ELSE 'micro'
        END as size_bucket,
        CASE
            WHEN (t.expiration - s.trade_date) = 0              THEN '0DTE'
            WHEN (t.expiration - s.trade_date) = 1              THEN 'Next-Day'
            WHEN (t.expiration - s.trade_date) BETWEEN 2 AND 3  THEN '2-3 DTE'
            WHEN (t.expiration - s.trade_date) BETWEEN 4 AND 7  THEN 'Weekly'
            WHEN (t.expiration - s.trade_date) BETWEEN 8 AND 30 THEN '1 Month'
            WHEN (t.expiration - s.trade_date) BETWEEN 31 AND 90 THEN '3 Months'
            WHEN (t.expiration - s.trade_date) > 90             THEN 'Long-term'
        END as dte_bucket
    FROM options_trades t
    JOIN sessions s
        ON t.sip_timestamp >= s.open_ts
       AND t.sip_timestamp <  s.cutoff_ts
    WHERE t.underlying = %(symbol)s
      AND t.sip_timestamp >= %(start_ts)s::timestamptz
      AND t.sip_timestamp <  %(end_ts)s::timestamptz
    ORDER BY t.sip_timestamp
"""

BARS_QUERY = """
    SELECT time, close
    FROM minute_bars
    WHERE symbol = %(symbol)s
      AND time >= %(start_ts)s::timestamptz
      AND time <  %(end_ts)s::timestamptz
    ORDER BY time
"""


# ── gap-fill ──────────────────────────────────────────────────────────────────

def apply_gap_fill(trades_df: pd.DataFrame, bars_df: pd.DataFrame) -> pd.DataFrame:
    """Assign underlying close via backward lookback on minute bars. No forward look."""
    if trades_df.empty or bars_df.empty:
        trades_df["underlying_close"] = None
        return trades_df

    trades_df = trades_df.sort_values("sip_timestamp").copy()
    bars_df   = bars_df.sort_values("time").copy()

    trades_ts = pd.to_datetime(trades_df["sip_timestamp"], utc=True)
    trades_df["sip_ts_naive"] = trades_ts.dt.tz_localize(None)
    bars_ts = pd.to_datetime(bars_df["time"], utc=True)
    bars_df["time_naive"] = bars_ts.dt.tz_localize(None)

    bars_df["bar_date"] = bars_df["time_naive"].dt.normalize()
    first_bars = (bars_df.groupby("bar_date").first().reset_index()
                  .rename(columns={"close": "first_close", "time_naive": "first_time"}))

    trades_df = pd.merge_asof(
        trades_df,
        bars_df[["time_naive", "close"]].rename(columns={"close": "lookback_close"}),
        left_on="sip_ts_naive", right_on="time_naive",
        direction="backward",       # NEVER forward — no look-ahead
    )
    trades_df["trade_date_dt"] = pd.to_datetime(trades_df["trade_date"]).dt.normalize()
    trades_df = trades_df.merge(
        first_bars[["bar_date", "first_close", "first_time"]],
        left_on="trade_date_dt", right_on="bar_date", how="left",
    )

    same_day = trades_df["time_naive"].notna() & (
        trades_df["time_naive"].dt.normalize() == trades_df["trade_date_dt"]
    )
    trades_df["underlying_close"] = (trades_df["lookback_close"].where(same_day, trades_df["first_close"])
                                     .fillna(trades_df["first_close"]))

    drop = ["time_naive", "lookback_close", "trade_date_dt", "bar_date",
            "first_close", "first_time", "sip_ts_naive"]
    trades_df.drop(columns=[c for c in drop if c in trades_df.columns], inplace=True)
    return trades_df


# ── aggregation ───────────────────────────────────────────────────────────────

def compute_aggregates(trades_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Intrinsic + extrinsic + total premium by (size_bucket, dte_bucket).

    Splits: strikes already post-split in options_trades; underlying_close from
    minute_bars is the live price at trade time. Intrinsic = max(K-S,0) correct.
    """
    if trades_df.empty:
        return pd.DataFrame()

    df       = trades_df.copy()
    has_price = df["underlying_close"].notna()

    intrinsic = pd.Series(0.0, index=df.index)
    call_idx  = df.index[df["option_type"].eq("call") & has_price]
    put_idx   = df.index[df["option_type"].eq("put")  & has_price]

    if len(call_idx):
        intrinsic.loc[call_idx] = (df.loc[call_idx, "underlying_close"] - df.loc[call_idx, "strike"]).clip(lower=0)
    if len(put_idx):
        intrinsic.loc[put_idx]  = (df.loc[put_idx,  "strike"] - df.loc[put_idx,  "underlying_close"]).clip(lower=0)

    df["intrinsic"]      = intrinsic
    df["extrinsic"]      = (df["price"] - intrinsic).clip(lower=0)
    df["extrinsic_flow"] = df["extrinsic"] * 100 * df["size"]
    df["intrinsic_flow"] = intrinsic         * 100 * df["size"]
    df["total_flow"]     = df["price"]       * 100 * df["size"]

    for col in ("extrinsic_flow", "intrinsic_flow", "total_flow"):
        df.loc[~has_price, col] = 0

    df = df[df["dte_bucket"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    df["is_itm"] = intrinsic[df.index] > 0
    is_put  = df["option_type"].eq("put")
    is_call = df["option_type"].eq("call")

    for prefix, mask in [("put", is_put), ("call", is_call)]:
        df[f"{prefix}_extrinsic"] = df["extrinsic_flow"].where(mask, 0)
        df[f"{prefix}_intrinsic"] = df["intrinsic_flow"].where(mask, 0)
        df[f"{prefix}_total"]     = df["total_flow"].where(mask, 0)
        df[f"{prefix}_itm"]       = (mask & df["is_itm"]).astype(int)
        df[f"{prefix}_trades"]    = mask.astype(int)

    agg = df.groupby(["trade_date", "size_bucket", "dte_bucket"]).agg(
        put_extrinsic_mm  =("put_extrinsic",  "sum"),
        call_extrinsic_mm =("call_extrinsic", "sum"),
        put_intrinsic_mm  =("put_intrinsic",  "sum"),
        call_intrinsic_mm =("call_intrinsic", "sum"),
        put_total_mm      =("put_total",      "sum"),
        call_total_mm     =("call_total",     "sum"),
        put_itm_trades    =("put_itm",        "sum"),
        call_itm_trades   =("call_itm",       "sum"),
        put_trades        =("put_trades",     "sum"),
        call_trades       =("call_trades",    "sum"),
    ).reset_index()

    for col in ("put_extrinsic_mm", "call_extrinsic_mm", "put_intrinsic_mm",
                "call_intrinsic_mm", "put_total_mm", "call_total_mm"):
        agg[col] /= 1e6

    agg["net_extrinsic_mm"] = agg["put_extrinsic_mm"] - agg["call_extrinsic_mm"]
    agg["net_total_mm"]     = agg["put_total_mm"]     - agg["call_total_mm"]
    agg["net_intrinsic_mm"] = agg["put_intrinsic_mm"] - agg["call_intrinsic_mm"]
    agg["symbol"] = symbol

    cols = [
        "trade_date", "symbol", "size_bucket", "dte_bucket",
        "put_extrinsic_mm", "call_extrinsic_mm", "net_extrinsic_mm",
        "put_total_mm", "call_total_mm", "net_total_mm",
        "put_intrinsic_mm", "call_intrinsic_mm", "net_intrinsic_mm",
        "put_itm_trades", "call_itm_trades", "put_trades", "call_trades",
    ]
    return agg[[c for c in cols if c in agg.columns]]


# ── worker (subprocess — must be top-level for pickling) ─────────────────────

def _worker(args: tuple) -> dict:
    """Process a single symbol in a subprocess with its own DB connection.

    Flushes accumulated day-DataFrames to disk every FLUSH_DAYS days
    to bound per-worker RAM. Atomic rename at the end.
    """
    symbol, start_date, end_date, output_dir_str, flush_days, cutoff_time = args

    # Ensure utils importable in subprocess
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from utils import get_db_connection as _get_conn  # noqa: F811

    output_dir = Path(output_dir_str)
    out_path   = output_dir / f"{symbol}_flow.parquet"
    tmp_path   = output_dir / f"{symbol}_flow.tmp.parquet"

    t0 = time.time()
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("SET timezone = 'UTC'")  # defensive: naive time col is UTC
        cur.execute("SET work_mem = '256MB'")
        cur.close()

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end   = datetime.strptime(end_date,   "%Y-%m-%d")

        # Build 1-day chunks
        chunks = []
        cur_date = start
        while cur_date < end:
            nxt = cur_date + timedelta(days=1)
            chunks.append((cur_date.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
            cur_date = nxt

        accumulated = []
        rows_total  = 0
        days_since_flush = 0

        for chunk_start, chunk_end in chunks:
            params = {
                "symbol": symbol,
                "cutoff_time": cutoff_time,
                "start_date": chunk_start,
                "end_date": chunk_end,
                "start_ts": f"{chunk_start}T00:00:00+00",
                "end_ts":   f"{chunk_end}T00:00:00+00",
            }

            trades_df = pd.read_sql(TRADES_QUERY, conn, params=params)
            bars_df   = pd.read_sql(BARS_QUERY,   conn, params=params)

            if not trades_df.empty:
                trades_df = apply_gap_fill(trades_df, bars_df)
                agg_df    = compute_aggregates(trades_df, symbol)
                if not agg_df.empty:
                    accumulated.append(agg_df)
                    rows_total += len(agg_df)

            days_since_flush += 1

            # Periodic flush to bound RAM
            if days_since_flush >= flush_days and accumulated:
                chunk_df = pd.concat(accumulated, ignore_index=True)
                if tmp_path.exists():
                    existing = pd.read_parquet(tmp_path)
                    chunk_df = pd.concat([existing, chunk_df], ignore_index=True)
                chunk_df.to_parquet(tmp_path, index=False)
                accumulated = []
                days_since_flush = 0

        # Final flush
        if accumulated:
            chunk_df = pd.concat(accumulated, ignore_index=True)
            if tmp_path.exists():
                existing = pd.read_parquet(tmp_path)
                chunk_df = pd.concat([existing, chunk_df], ignore_index=True)
            chunk_df.to_parquet(tmp_path, index=False)

        conn.close()

        # Atomic rename
        if tmp_path.exists():
            tmp_path.rename(out_path)

        return {"symbol": symbol, "status": "success", "rows": rows_total,
                "elapsed": time.time() - t0}

    except Exception as e:
        return {"symbol": symbol, "status": "error", "error": str(e)[:200],
                "elapsed": time.time() - t0}


# ── combine ───────────────────────────────────────────────────────────────────

def combine_outputs(output_dir: Path) -> None:
    """Merge all per-symbol parquets into flow_combined.parquet."""
    files = find_flow_files(output_dir, exclude_combined=True)
    if not files:
        print("No flow files to combine.")
        return

    combined = load_flow_files_with_progress(files)
    out_path = output_dir / "flow_combined.parquet"
    combined.to_parquet(out_path, index=False)

    print(f"\nCombined dataset:")
    print(f"  Symbols:    {sorted(combined['symbol'].unique())}")
    print(f"  Date range: {combined['trade_date'].min().date()} -> {combined['trade_date'].max().date()}")
    print(f"  Total rows: {len(combined):,}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Universal flow cache — full intrinsic/extrinsic decomposition"
    )
    parser.add_argument("--symbols",       nargs="+", default=None,
                        help="Specific symbols (overrides symbols.txt)")
    parser.add_argument("--symbols-file",  type=str, default=str(HERE / "symbols.txt"),
                        help="Path to symbols file (default: symbols.txt)")
    parser.add_argument("--start-date",    default=DEFAULT_START_DATE)
    parser.add_argument("--end-date",      default=DEFAULT_END_DATE)
    parser.add_argument("--workers",       type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip symbols that already have a completed parquet")
    parser.add_argument("--combine-only",  action="store_true")
    parser.add_argument("--cutoff-time",   default=CUTOFF_TIME,
                        help="Market cutoff HH:MM ET (default: 15:30)")
    args = parser.parse_args()

    output_dir = HERE / "output"
    output_dir.mkdir(exist_ok=True)

    if args.combine_only:
        combine_outputs(output_dir)
        return

    # Load symbols
    if args.symbols:
        symbols = args.symbols
    else:
        symbols = load_symbols(Path(args.symbols_file))
        print(f"Loaded {len(symbols)} symbols from {args.symbols_file}")

    # Skip existing
    if args.skip_existing:
        done = {p.stem.replace("_flow", "") for p in output_dir.glob("*_flow.parquet")
                if "combined" not in p.name and "tmp" not in p.name}
        original = len(symbols)
        symbols = [s for s in symbols if s not in done]
        print(f"Skipping {original - len(symbols)} existing, {len(symbols)} remaining")

    if not symbols:
        print("All symbols already processed!")
        combine_outputs(output_dir)
        return

    n_workers = min(args.workers, len(symbols))

    # Log splits in date range (audit requirement)
    conn = get_db_connection()
    log_splits_in_range(conn, symbols, args.start_date, args.end_date)
    conn.close()

    print()
    print("=" * 70)
    print("Universal Flow Cache — Full Intrinsic/Extrinsic Decomposition")
    print("=" * 70)
    print(f"Symbols:    {len(symbols)}  ({', '.join(symbols[:5])}{'...' if len(symbols) > 5 else ''})")
    print(f"Date range: {args.start_date} -> {args.end_date}")
    print(f"Cutoff:     {args.cutoff_time} ET")
    print(f"Workers:    {n_workers}")
    print(f"Flush:      every {FLUSH_DAYS} days per worker")
    print(f"Output:     {output_dir}")
    print(f"Columns:    17 per row (3 value types x 2 sides + net + ITM counts)")
    print(f"Buckets:    7 size x 7 DTE = 49 combos/day/symbol")
    print("=" * 70)
    print()

    work_items = [
        (sym, args.start_date, args.end_date, str(output_dir), FLUSH_DAYS, args.cutoff_time)
        for sym in symbols
    ]

    t_start  = time.time()
    failed   = []
    total_rows = 0

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_worker, item): item[0] for item in work_items}
        with tqdm(total=len(symbols), desc="Generating flow") as pbar:
            for future in as_completed(futures):
                result = future.result()
                sym     = result["symbol"]
                elapsed = result.get("elapsed", 0)

                if result["status"] == "success":
                    rows = result.get("rows", 0)
                    total_rows += rows
                    tqdm.write(f"  {sym:6s}  {rows:>7,} rows  ({elapsed:.0f}s)")
                elif result["status"] == "error":
                    failed.append(sym)
                    tqdm.write(f"  {sym:6s}  ERROR: {result.get('error', '?')}")

                pbar.update(1)

    elapsed_total = time.time() - t_start
    print(f"\nDone in {elapsed_total/60:.1f} min — {total_rows:,} total rows")
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")

    # Combine all into flow_combined.parquet
    combine_outputs(output_dir)


if __name__ == "__main__":
    main()
