"""
Utilities for SPY/QQQ options flow generation.

This module provides database connectivity, stock-split auditing, and file I/O
helpers shared across the flow generation pipeline. Adapted from
spy_flow_spreads_original with scope narrowed to SPY and QQQ only.

Why SPY and QQQ only?
    These two ETFs dominate US options volume — together they account for
    roughly 40-50% of all equity options traded daily. They have the deepest
    liquidity, tightest spreads, and daily expirations (MWF + some Tue/Thu),
    making them the best candidates for flow-based signal research.
"""
import glob
import os
import warnings
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from tqdm import tqdm

# Load .env from the research/ directory (two levels up from this file).
# This lets us keep credentials out of source while still having a convenient
# local dev setup. The .env file is gitignored; .env.example shows the format.
# python-dotenv is optional — if not installed, DATABASE_URL must be set
# via the shell environment (e.g. in CI or container environments).
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Only SPY and QQQ — highest-volume ETFs with daily expirations
ETF_SYMBOLS = ["SPY", "QQQ"]

# Size buckets classify trade size (number of contracts per fill).
# These ranges capture the spectrum from retail (micro/small) to
# institutional (large/mega/ultra) activity.
SIZE_BUCKETS = ["micro", "small", "medium", "block", "large", "mega", "ultra"]

# DTE (Days To Expiration) buckets — finer granularity for near-term.
#
# SPY/QQQ have daily expirations, so 0DTE, Next-Day, and 2-3 DTE options
# all trade with high daily volume. Separating them from the 4-7 day
# "Weekly" bucket lets downstream analysis isolate:
#   - 0DTE:      same-day gamma hedging and directional bets
#   - Next-Day:  overnight positioning (1 DTE)
#   - 2-3 DTE:   short-term swing flow
#   - Weekly:    end-of-week positioning (4-7 DTE)
#   - 1 Month+:  longer-term hedging and structural positions
DTE_BUCKETS = ["0DTE", "Next-Day", "2-3 DTE", "Weekly", "1 Month", "3 Months", "Long-term"]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# DATABASE_URL is read from the environment (set via .env or shell export).
# The check is deferred to get_db_connection() so that importing this module
# for constants like DTE_BUCKETS or SIZE_BUCKETS works without a database.


def get_db_connection() -> Any:
    """Create a new database connection using DATABASE_URL from the environment.

    Raises EnvironmentError if DATABASE_URL is not set.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "DATABASE_URL environment variable is required. "
            "Example: export DATABASE_URL=postgresql://user:pass@localhost:5432/dbname\n"
            "See .env.example for the expected format."
        )
    return psycopg2.connect(url)


# ---------------------------------------------------------------------------
# Stock Splits
# ---------------------------------------------------------------------------


def get_splits_for_symbols(
    conn: Any,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Query stock splits for given symbols and date range.

    Stock splits affect option strike prices and underlying prices.
    We track them explicitly so the flow aggregation pipeline can
    audit whether any splits occurred during the generation window.
    """
    if not symbols:
        return pd.DataFrame(columns=["symbol", "split_date", "split_ratio"])

    placeholders = ",".join(["%s"] * len(symbols))
    query = f"""
        SELECT symbol, split_date, split_ratio::float
        FROM stock_splits
        WHERE symbol IN ({placeholders})
          AND split_date >= %s
          AND split_date < %s
        ORDER BY symbol, split_date
    """
    params = tuple(symbols) + (start_date, end_date)
    return pd.read_sql(query, conn, params=params)


def log_splits_in_range(
    conn: Any,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, list[tuple[date, float]]]:
    """Log any stock splits in the date range for audit purposes.

    This runs before flow generation starts so the user can see
    whether splits might affect the data. The flow pipeline itself
    does NOT adjust for splits (options strikes are already split-adjusted
    in the raw data), but it's important to know when they happened.
    """
    splits_df = get_splits_for_symbols(conn, symbols, start_date, end_date)

    if splits_df.empty:
        print(f"No stock splits in range {start_date} to {end_date} for these symbols")
        return {}

    splits_by_symbol: dict[str, list[tuple[date, float]]] = {}
    for _, row in splits_df.iterrows():
        sym = row["symbol"]
        if sym not in splits_by_symbol:
            splits_by_symbol[sym] = []
        splits_by_symbol[sym].append((row["split_date"], row["split_ratio"]))

    print(f"Stock splits in range {start_date} to {end_date}:")
    for sym, splits in sorted(splits_by_symbol.items()):
        for split_date, ratio in splits:
            print(f"  {sym}: {split_date} ({ratio}:1 split)")

    return splits_by_symbol


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def get_output_dir(script_file: str) -> Path:
    """Get output directory relative to the calling script's location."""
    return Path(script_file).parent / "output"


def find_flow_files(
    output_dir: Path,
    pattern: str = "*_flow.parquet",
    exclude_combined: bool = True,
) -> list[str]:
    """Find flow parquet files in the output directory.

    By default excludes the combined file so we only get per-symbol files.
    """
    files = glob.glob(str(output_dir / pattern))
    if exclude_combined:
        files = [f for f in files if "flow_combined" not in f]
    return files


def load_flow_files_with_progress(
    files: list[str],
    desc: str = "Loading",
) -> pd.DataFrame:
    """Load parquet files with a progress bar and concatenate into one DataFrame."""
    dfs = []
    for f in tqdm(files, desc=desc):
        try:
            df = pd.read_parquet(f)
            if "trade_date" in df.columns:
                df["trade_date"] = pd.to_datetime(df["trade_date"])
            dfs.append(df)
        except Exception as e:
            print(f"Error reading {f}: {e}")
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)
