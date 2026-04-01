"""
Database Monitor — Live query monitoring, density profiling, and safety enforcement.

This module provides:
1. Symbol density profiles (EXTREME/HIGH/MEDIUM/LOW) for query planning
2. Live query monitoring via pg_stat_activity
3. Connection management with automatic cleanup
4. Safe query execution with guardrails

Used by the orchestrator to watch experiment agents in real time.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2

DB_URL = os.environ.get("DATABASE_URL", "")

DENSITY_CACHE_PATH = Path(__file__).parent / "cache" / "symbol_density.json"


# ═══════════════════════════════════════════════════════════════════════════════
# Connection Safety
# ═══════════════════════════════════════════════════════════════════════════════


@contextlib.contextmanager
def safe_connection(db_url: str = DB_URL, statement_timeout: str = "30s"):
    """Context manager that guarantees connection cleanup and statement timeout."""
    conn = psycopg2.connect(db_url, connect_timeout=5)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{statement_timeout}'")
        yield conn
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Symbol Density Profiling
# ═══════════════════════════════════════════════════════════════════════════════

# Density tiers determine max query window:
#   EXTREME (>500K trades/day): 1 day max, always use cursor_engine
#   HIGH    (100K-500K/day):    1 day max for options, 5 days for minute_bars
#   MEDIUM  (10K-100K/day):     1 week for options, 1 month for minute_bars
#   LOW     (<10K/day):         1 month for options, 1 year for minute_bars

TIER_THRESHOLDS = {
    "EXTREME": 500_000,
    "HIGH": 100_000,
    "MEDIUM": 10_000,
}


@dataclass
class SymbolDensity:
    symbol: str
    options_daily_avg: int
    minute_bars_daily_avg: int
    tier: str
    max_options_window_days: int
    max_minute_bars_window_days: int


def _classify_tier(daily_avg: int) -> str:
    if daily_avg >= TIER_THRESHOLDS["EXTREME"]:
        return "EXTREME"
    if daily_avg >= TIER_THRESHOLDS["HIGH"]:
        return "HIGH"
    if daily_avg >= TIER_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def _max_window(tier: str, table: str) -> int:
    """Max query window in days for a given tier and table."""
    windows = {
        "EXTREME": {"options_trades": 1, "minute_bars": 1},
        "HIGH":    {"options_trades": 1, "minute_bars": 5},
        "MEDIUM":  {"options_trades": 7, "minute_bars": 30},
        "LOW":     {"options_trades": 30, "minute_bars": 365},
    }
    return windows.get(tier, windows["MEDIUM"]).get(table, 1)


def build_density_profile(conn, sample_start: str = "2024-06-01", sample_end: str = "2024-06-08") -> dict[str, SymbolDensity]:
    """Build density profile for all symbols from a 1-week sample.

    This queries the DB once and caches the result. Run it once at setup,
    not on every experiment.
    """
    sample_days = 5  # trading days in the sample week
    profiles: dict[str, SymbolDensity] = {}

    with conn.cursor() as cur:
        # Options density
        cur.execute("""
            SELECT underlying, count(*) / %s as daily_avg
            FROM options_trades
            WHERE sip_timestamp >= %s::timestamp AND sip_timestamp < %s::timestamp
            GROUP BY underlying
        """, (sample_days, sample_start, sample_end))
        options_density = {row[0]: int(row[1]) for row in cur.fetchall()}

        # Minute bars density
        cur.execute("""
            SELECT symbol, count(*) / %s as daily_avg
            FROM minute_bars
            WHERE time >= %s::timestamp AND time < %s::timestamp
            GROUP BY symbol
        """, (sample_days, sample_start, sample_end))
        bars_density = {row[0]: int(row[1]) for row in cur.fetchall()}

    all_symbols = set(options_density.keys()) | set(bars_density.keys())
    for symbol in all_symbols:
        opt_avg = options_density.get(symbol, 0)
        bar_avg = bars_density.get(symbol, 0)
        tier = _classify_tier(opt_avg)
        profiles[symbol] = SymbolDensity(
            symbol=symbol,
            options_daily_avg=opt_avg,
            minute_bars_daily_avg=bar_avg,
            tier=tier,
            max_options_window_days=_max_window(tier, "options_trades"),
            max_minute_bars_window_days=_max_window(tier, "minute_bars"),
        )

    return profiles


def save_density_profile(profiles: dict[str, SymbolDensity]) -> Path:
    """Cache density profiles to disk."""
    DENSITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        sym: {
            "options_daily_avg": p.options_daily_avg,
            "minute_bars_daily_avg": p.minute_bars_daily_avg,
            "tier": p.tier,
            "max_options_window_days": p.max_options_window_days,
            "max_minute_bars_window_days": p.max_minute_bars_window_days,
        }
        for sym, p in profiles.items()
    }
    DENSITY_CACHE_PATH.write_text(json.dumps(data, indent=2))
    return DENSITY_CACHE_PATH


def load_density_profile() -> dict[str, SymbolDensity] | None:
    """Load cached density profiles."""
    if not DENSITY_CACHE_PATH.exists():
        return None
    data = json.loads(DENSITY_CACHE_PATH.read_text())
    return {
        sym: SymbolDensity(symbol=sym, **vals)
        for sym, vals in data.items()
    }


def get_density(symbol: str, profiles: dict[str, SymbolDensity] | None = None) -> SymbolDensity:
    """Get density for a symbol. Falls back to MEDIUM if unknown."""
    if profiles is None:
        profiles = load_density_profile() or {}
    if symbol in profiles:
        return profiles[symbol]
    # Unknown symbol — assume MEDIUM (conservative)
    return SymbolDensity(
        symbol=symbol,
        options_daily_avg=50_000,
        minute_bars_daily_avg=500,
        tier="MEDIUM",
        max_options_window_days=7,
        max_minute_bars_window_days=30,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Live Query Monitor
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ActiveQuery:
    pid: int
    duration_seconds: float
    state: str
    query: str
    waiting: bool
    application_name: str


def get_active_queries(conn) -> list[ActiveQuery]:
    """Get all active queries on the database."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                pid,
                EXTRACT(EPOCH FROM (now() - query_start)) as duration_seconds,
                state,
                LEFT(query, 500) as query,
                wait_event IS NOT NULL as waiting,
                COALESCE(application_name, '') as application_name
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid != pg_backend_pid()
              AND state != 'idle'
            ORDER BY duration_seconds DESC
        """)
        return [
            ActiveQuery(
                pid=row[0],
                duration_seconds=float(row[1]) if row[1] else 0,
                state=row[2] or "unknown",
                query=row[3] or "",
                waiting=bool(row[4]),
                application_name=row[5],
            )
            for row in cur.fetchall()
        ]


def get_connection_count(conn) -> int:
    """Get total connection count."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        return cur.fetchone()[0]


def cancel_query(conn, pid: int) -> bool:
    """Gracefully cancel a query. Returns True if successful."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_cancel_backend(%s)", (pid,))
        return cur.fetchone()[0]


def terminate_connection(conn, pid: int) -> bool:
    """Forcefully terminate a connection. Returns True if successful."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        return cur.fetchone()[0]


def kill_long_queries(conn, max_seconds: float = 60) -> list[int]:
    """Kill any query running longer than max_seconds. Returns list of killed PIDs."""
    killed = []
    queries = get_active_queries(conn)
    for q in queries:
        if q.duration_seconds > max_seconds and q.state == "active":
            print(f"  KILLING pid={q.pid} ({q.duration_seconds:.0f}s): {q.query[:100]}", file=sys.stderr)
            if not cancel_query(conn, q.pid):
                time.sleep(2)
                terminate_connection(conn, q.pid)
            killed.append(q.pid)
    return killed


# ═══════════════════════════════════════════════════════════════════════════════
# Monitor Loop — Run as a background process during experiments
# ═══════════════════════════════════════════════════════════════════════════════


def monitor_loop(
    interval_seconds: int = 5,
    max_query_seconds: float = 60,
    max_connections: int = 4,
    auto_kill: bool = False,
):
    """Continuous monitoring loop. Run this in a background process while experiments execute.

    Prints warnings and optionally kills long-running queries.

    Args:
        interval_seconds: How often to check (default 5s)
        max_query_seconds: Warn/kill queries running longer than this (default 60s)
        max_connections: Warn when connection count exceeds this
        auto_kill: If True, automatically kill queries exceeding max_query_seconds
    """
    print(f"[db-monitor] Starting (interval={interval_seconds}s, max_query={max_query_seconds}s, "
          f"max_conn={max_connections}, auto_kill={auto_kill})", file=sys.stderr)

    while True:
        try:
            with safe_connection(statement_timeout="10s") as conn:
                # Check connection count
                count = get_connection_count(conn)
                if count > max_connections:
                    print(f"[db-monitor] WARNING: {count} connections (max {max_connections})", file=sys.stderr)

                # Check active queries
                queries = get_active_queries(conn)
                for q in queries:
                    if q.duration_seconds > max_query_seconds:
                        print(f"[db-monitor] LONG QUERY pid={q.pid} ({q.duration_seconds:.0f}s) "
                              f"state={q.state}: {q.query[:120]}", file=sys.stderr)
                        if auto_kill and q.state == "active":
                            print(f"[db-monitor] AUTO-KILLING pid={q.pid}", file=sys.stderr)
                            cancel_query(conn, q.pid)
                    elif q.duration_seconds > max_query_seconds / 2:
                        print(f"[db-monitor] SLOW pid={q.pid} ({q.duration_seconds:.0f}s): {q.query[:80]}",
                              file=sys.stderr)

        except Exception as e:
            print(f"[db-monitor] ERROR: {e}", file=sys.stderr)

        time.sleep(interval_seconds)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    """CLI entrypoint."""
    import argparse

    parser = argparse.ArgumentParser(description="Database monitor for causal signal research")
    sub = parser.add_subparsers(dest="command")

    # Profile command
    profile_cmd = sub.add_parser("profile", help="Build and cache symbol density profile")
    profile_cmd.add_argument("--sample-start", default="2024-06-01")
    profile_cmd.add_argument("--sample-end", default="2024-06-08")

    # Monitor command
    monitor_cmd = sub.add_parser("monitor", help="Live query monitoring")
    monitor_cmd.add_argument("--interval", type=int, default=5, help="Check interval in seconds")
    monitor_cmd.add_argument("--max-query", type=float, default=60, help="Max query duration before warning")
    monitor_cmd.add_argument("--max-connections", type=int, default=4)
    monitor_cmd.add_argument("--auto-kill", action="store_true", help="Automatically kill long queries")

    # Status command
    sub.add_parser("status", help="One-shot status check")

    # Kill command
    kill_cmd = sub.add_parser("kill", help="Kill all experiment queries")
    kill_cmd.add_argument("--max-seconds", type=float, default=30)

    # Density command
    density_cmd = sub.add_parser("density", help="Look up symbol density")
    density_cmd.add_argument("symbol", help="Symbol to look up")

    args = parser.parse_args()

    if args.command == "profile":
        print("Building density profile...", file=sys.stderr)
        with safe_connection(statement_timeout="120s") as conn:
            profiles = build_density_profile(conn, args.sample_start, args.sample_end)
            path = save_density_profile(profiles)
        print(f"Cached {len(profiles)} symbols to {path}")
        # Print tier summary
        tiers = {}
        for p in profiles.values():
            tiers.setdefault(p.tier, []).append(p.symbol)
        for tier in ["EXTREME", "HIGH", "MEDIUM", "LOW"]:
            symbols = tiers.get(tier, [])
            print(f"  {tier}: {len(symbols)} symbols", end="")
            if symbols and len(symbols) <= 10:
                print(f" ({', '.join(sorted(symbols))})")
            else:
                print()

    elif args.command == "monitor":
        monitor_loop(
            interval_seconds=args.interval,
            max_query_seconds=args.max_query,
            max_connections=args.max_connections,
            auto_kill=args.auto_kill,
        )

    elif args.command == "status":
        with safe_connection(statement_timeout="10s") as conn:
            count = get_connection_count(conn)
            queries = get_active_queries(conn)
            print(f"Connections: {count}")
            if queries:
                print(f"Active queries: {len(queries)}")
                for q in queries:
                    print(f"  pid={q.pid} {q.duration_seconds:.1f}s {q.state}: {q.query[:100]}")
            else:
                print("No active queries")

    elif args.command == "kill":
        with safe_connection(statement_timeout="10s") as conn:
            killed = kill_long_queries(conn, args.max_seconds)
            if killed:
                print(f"Killed {len(killed)} queries: {killed}")
            else:
                print("No long-running queries to kill")

    elif args.command == "density":
        profiles = load_density_profile()
        if profiles is None:
            print("No density profile cached. Run: python -m shared.db_monitor profile")
            sys.exit(1)
        d = get_density(args.symbol, profiles)
        print(f"{d.symbol}: tier={d.tier}")
        print(f"  options: {d.options_daily_avg:,}/day, max window: {d.max_options_window_days} days")
        print(f"  minute_bars: {d.minute_bars_daily_avg:,}/day, max window: {d.max_minute_bars_window_days} days")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
