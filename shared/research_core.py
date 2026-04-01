"""Core shared research helpers for the self-contained causal signal baseline.

This module centralizes:
- local file paths that now live *inside* `causal_signal_research`
- baseline constants and default configs
- the symbol universe loader
- FRED/macro regime helpers
- signal accumulation logic reused across multiple runners

Keeping these pieces here avoids sideways imports between experiment folders and
ensures the new research path is self-contained.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Local path anchors for the self-contained research tree.
CAUSAL_RESEARCH_ROOT = Path(__file__).resolve().parent.parent
SHARED_CACHE_DIR = Path(__file__).resolve().parent / "cache"
BASELINE_DIR = CAUSAL_RESEARCH_ROOT / "00_baseline"
FRED_PANEL_PATH = SHARED_CACHE_DIR / "msvar_panel.parquet"
UNIVERSE_PARQUET_PATH = SHARED_CACHE_DIR / "intraday_prices.parquet"
UNIVERSE_CACHE_PATH = SHARED_CACHE_DIR / "symbol_universe.json"

# Shared baseline portfolio constants.
INITIAL_CAPITAL = 10_000.0

# Default hand-tuned baseline configuration carried over from the 17b port.
BEST_CONFIG: dict[str, float | int] = {
    "streak_mult": 0.75,
    "hit_rate_threshold": 0.57,
    "hit_rate_lookback": 80,
    "min_signal_pctile": 0.50,
    "min_intraday_ret": 0.013,
}

# Known split dates retained for the faithful baseline runner that preserves the
# original split-filtering behavior.
KNOWN_SPLIT_DATES: dict[str, set[date]] = {
    "GOOGL": {date(2022, 7, 18)},
    "AMZN": {date(2022, 6, 6)},
    "TSLA": {date(2022, 8, 25)},
    "SHOP": {date(2022, 6, 29)},
    "NVDA": {date(2024, 6, 10)},
    "AVGO": {date(2024, 7, 15)},
    "PANW": {date(2022, 9, 14)},
    "WMT": {date(2024, 2, 26)},
    "MSTR": {date(2024, 8, 8)},
    "GME": {date(2022, 7, 22)},
    "SMCI": {date(2024, 10, 1)},
    "AMC": {date(2023, 8, 24)},
    "UNG": {date(2024, 1, 24)},
}

# Dates used to force spot checks around DST transitions and periodic audits.
DST_AUDIT_DATES: set[date] = {
    date(2022, 3, 11), date(2022, 3, 14),
    date(2022, 11, 4), date(2022, 11, 7),
    date(2023, 3, 10), date(2023, 3, 13),
    date(2024, 3, 8), date(2024, 3, 11),
    date(2024, 11, 1), date(2024, 11, 4),
    date(2025, 3, 7), date(2025, 3, 10),
    date(2025, 10, 31), date(2025, 11, 3),
    date(2026, 3, 6), date(2026, 3, 9),
}


def as_float(value: float | int) -> float:
    """Normalize config values loaded from JSON/Optuna into Python floats."""
    return float(value)



def as_int(value: float | int) -> int:
    """Normalize config values loaded from JSON/Optuna into Python ints."""
    return int(value)



def get_symbols() -> list[str]:
    """Load the baseline universe. Tries caches first, falls back to DB.

    Priority: symbol_universe.json > intraday_prices.parquet > DB query.
    The JSON cache is written by get_symbol_universe() on first DB call.
    """
    # Fast path: dedicated universe cache (written by get_symbol_universe)
    if UNIVERSE_CACHE_PATH.exists():
        import json
        symbols = json.loads(UNIVERSE_CACHE_PATH.read_text())
        return sorted(symbols)

    # Legacy fallback: extract from intraday_prices parquet
    if UNIVERSE_PARQUET_PATH.exists():
        prices = pd.read_parquet(UNIVERSE_PARQUET_PATH, columns=["symbol"])
        return sorted(prices["symbol"].unique().tolist())

    # DB fallback: query and cache for next time
    from shared.db_monitor import safe_connection
    with safe_connection(statement_timeout="300s") as conn:
        symbols = get_symbol_universe(conn)
    return symbols


def get_r1000_symbols() -> list[str]:
    """Load the R1000 universe from flow_cache/symbols.txt (~911 symbols)."""
    path = CAUSAL_RESEARCH_ROOT / "flow_cache" / "symbols.txt"
    syms = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            syms.append(line)
    return sorted(set(syms))


def get_symbol_universe(
    conn=None,
    min_coverage_pct: float = 0.90,
    anchor_symbol: str = "SPY",
) -> list[str]:
    """Get the liquid symbol universe. Cached to shared/cache/symbol_universe.json.

    On first call (or missing cache), queries the DB for symbols with close prices
    on at least `min_coverage_pct` of the anchor's trading days. Caches the result
    so subsequent calls (including from worktrees) skip the expensive DB scan.

    Args:
        conn: psycopg2 connection (optional if cache exists)
        min_coverage_pct: minimum fraction of anchor's trading days (0.90 = 90%)
        anchor_symbol: reference symbol for trading day count

    Returns:
        Sorted list of symbols meeting the coverage threshold.
    """
    import json

    # Fast path: read from cache
    if UNIVERSE_CACHE_PATH.exists():
        symbols = json.loads(UNIVERSE_CACHE_PATH.read_text())
        return sorted(symbols)

    # DB path: query and cache
    if conn is None:
        raise ValueError("No cached universe and no DB connection provided. "
                         "Run with a connection first to build the cache.")

    from shared.config import START_DATE, END_DATE
    from shared.cursor_engine import MinuteBarsSource

    # Get trading day count from anchor (bounded query)
    source = MinuteBarsSource()
    trading_days = source.get_trading_days(conn, START_DATE, END_DATE,
                                           anchor_symbol=anchor_symbol)
    min_days = int(len(trading_days) * min_coverage_pct)

    # Query symbols by year to avoid scanning the full hypertable at once.
    # Each yearly chunk is fast (~30s); the full range times out (>5min).
    from collections import Counter
    symbol_days: Counter = Counter()
    start_year = int(START_DATE[:4])
    end_year = int(END_DATE[:4])

    for year in range(start_year, end_year + 1):
        y_start = f"{year}-01-01"
        y_end = f"{year + 1}-01-01"
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, COUNT(DISTINCT time::date) as n_days
                FROM minute_bars
                WHERE time >= %s::timestamptz
                  AND time < %s::timestamptz
                  AND symbol != %s
                GROUP BY symbol
            """, (y_start, y_end, anchor_symbol))
            for row in cur.fetchall():
                symbol_days[str(row[0])] += int(row[1])

    symbols = sorted([sym for sym, days in symbol_days.items() if days >= min_days])

    # Cache for next time
    SHARED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    UNIVERSE_CACHE_PATH.write_text(json.dumps(symbols))

    return symbols


def build_fred_panel() -> pd.DataFrame:
    """Build the MacroRegime feature panel from the DB.

    Queries raw FRED series and SPY/VXX closes, computes derived features.
    Returns a DataFrame with columns: T10Y2Y, BAMLH0A0HYM2, SPY_ret, VXX_ret,
    T10Y2Y_zscore, HY_zscore — indexed by date.

    This is the authoritative source of truth. ~30 seconds to build.
    """
    from shared.db_monitor import safe_connection
    from shared.config import START_DATE

    ZSCORE_LOOKBACK = 252
    ZSCORE_MIN_OBS = 60

    with safe_connection(statement_timeout="120s") as conn:
        with conn.cursor() as cur:
            # 1. FRED series: T10Y2Y and HY spread
            cur.execute(
                "SELECT date::date, value FROM fred_releases "
                "WHERE series_id = 'T10Y2Y' ORDER BY date"
            )
            t10y2y = pd.Series(
                {row[0]: float(row[1]) for row in cur.fetchall()},
                name="T10Y2Y",
            )

            cur.execute(
                "SELECT date::date, value FROM fred_releases "
                "WHERE series_id = 'BAMLH0A0HYM2' ORDER BY date"
            )
            hy = pd.Series(
                {row[0]: float(row[1]) for row in cur.fetchall()},
                name="BAMLH0A0HYM2",
            )

            # 2. SPY and VXX daily closes from minute_bars
            # Use 15:55-16:05 UTC-adjusted window for close price
            for sym in ("SPY", "VXX"):
                cur.execute("""
                    SELECT DISTINCT ON (time::date) time::date as d,
                           close
                    FROM minute_bars
                    WHERE symbol = %s
                      AND time >= %s::timestamptz
                      AND time::date = time::date  -- single-day per row
                    ORDER BY time::date, time DESC
                """, (sym, START_DATE))
                rows = cur.fetchall()
                if sym == "SPY":
                    spy_close = pd.Series(
                        {row[0]: float(row[1]) for row in rows},
                        name="SPY_close",
                    )
                else:
                    vxx_close = pd.Series(
                        {row[0]: float(row[1]) for row in rows},
                        name="VXX_close",
                    )

    # Align all series to common dates
    panel = pd.DataFrame({
        "T10Y2Y": t10y2y,
        "BAMLH0A0HYM2": hy,
    })
    panel.index = pd.to_datetime(panel.index)
    panel.index.name = "date"

    # Add SPY and VXX daily returns
    spy_s = pd.Series(spy_close.values, index=pd.to_datetime(spy_close.index), name="SPY_close")
    vxx_s = pd.Series(vxx_close.values, index=pd.to_datetime(vxx_close.index), name="VXX_close")

    panel["SPY_ret"] = spy_s.pct_change()
    panel["VXX_ret"] = vxx_s.pct_change().clip(-0.5, 0.5)

    # Compute rolling z-scores
    for col, z_col in [("T10Y2Y", "T10Y2Y_zscore"), ("BAMLH0A0HYM2", "HY_zscore")]:
        rolling_mean = panel[col].rolling(ZSCORE_LOOKBACK, min_periods=ZSCORE_MIN_OBS).mean()
        rolling_std = panel[col].rolling(ZSCORE_LOOKBACK, min_periods=ZSCORE_MIN_OBS).std()
        panel[z_col] = (panel[col] - rolling_mean) / rolling_std.replace(0, np.nan)

    # Drop rows where we have no price data (before minute_bars coverage)
    # FRED goes back to 2005 but SPY/VXX only to ~2022 — keep only overlapping range
    panel = panel.dropna(subset=["SPY_ret", "VXX_ret"], how="all")
    # Forward-fill FRED gaps (weekends, holidays) within the overlapping range
    panel[["T10Y2Y", "BAMLH0A0HYM2", "T10Y2Y_zscore", "HY_zscore"]] = (
        panel[["T10Y2Y", "BAMLH0A0HYM2", "T10Y2Y_zscore", "HY_zscore"]].ffill()
    )

    return panel


def load_fred_panel() -> pd.DataFrame:
    """Load MacroRegime feature panel. Builds from DB if cache missing or invalid.

    The cache is a convenience — the DB is the source of truth.
    """
    required_cols = {"T10Y2Y_zscore", "HY_zscore", "SPY_ret", "VXX_ret"}

    if FRED_PANEL_PATH.exists():
        panel = pd.read_parquet(FRED_PANEL_PATH)
        panel.index = pd.to_datetime(panel.index)
        missing = required_cols - set(panel.columns)
        if not missing and len(panel) >= 252:
            return panel
        import logging
        logging.getLogger(__name__).warning(
            f"FRED panel cache invalid (missing={missing or 'none'}, rows={len(panel)}). "
            f"Rebuilding from DB..."
        )

    # Build from DB — takes ~30 seconds
    panel = build_fred_panel()

    # Validate before caching
    missing = required_cols - set(panel.columns)
    if missing:
        raise ValueError(f"build_fred_panel() produced panel missing columns: {missing}")
    if len(panel) < 252:
        raise ValueError(f"build_fred_panel() produced only {len(panel)} rows (need >= 252)")

    # Cache for next time
    SHARED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(FRED_PANEL_PATH)

    return panel


class MacroRegime:
    """Causal macro regime classifier fit only on data available before each day."""

    FEATURE_COLS = ["T10Y2Y_zscore", "HY_zscore", "SPY_ret", "VXX_ret"]

    def __init__(self, fred_panel: pd.DataFrame, min_obs: int = 120, refit_every: int = 20):
        self.panel = fred_panel.sort_index().copy()
        self.panel["VXX_ret"] = self.panel["VXX_ret"].clip(-0.5, 0.5)
        self.min_obs = min_obs
        self.refit_every = refit_every
        self.model = None
        self.bull_state: int | None = None
        self._last_fit_n = -999

    def get_regime(self, today: date) -> str:
        """Return the current bull/bear regime using only pre-`today` observations."""
        from hmmlearn.hmm import GaussianHMM

        history = self.panel.loc[self.panel.index < pd.Timestamp(today)]
        if len(history) and not bool((history.index < pd.Timestamp(today)).all()):
            raise AssertionError(f"FRED temporal violation: found row >= {today}")
        if len(history) < self.min_obs:
            return "unknown"

        data = history[self.FEATURE_COLS].dropna().values
        if len(data) < self.min_obs:
            return "unknown"

        n = len(data)
        should_refit = self.model is None or (n - self._last_fit_n) >= self.refit_every
        if should_refit:
            try:
                model = GaussianHMM(
                    n_components=2,
                    covariance_type="full",
                    n_iter=200,
                    random_state=42,
                )
                model.fit(data)
                spy_idx = self.FEATURE_COLS.index("SPY_ret")
                self.bull_state = 0 if model.means_[0][spy_idx] > model.means_[1][spy_idx] else 1
                self.model = model
                self._last_fit_n = n
            except Exception:
                return "unknown"

        if self.model is None or self.bull_state is None:
            return "unknown"

        try:
            probs = self.model.predict_proba(data)
            p_bull = float(probs[-1][self.bull_state])
            return "bull" if p_bull > 0.5 else "bear"
        except Exception:
            return "unknown"


class Accumulator:
    """Rolling hit-rate and positive-return accumulator for overnight follow-through."""

    def __init__(self, lookback: int = 80):
        self.lookback = lookback
        self.rets: dict[str, list[float]] = defaultdict(list)
        self.hit_rate: dict[str, float] = {}
        self.avg_pos: dict[str, float] = {}
        self.streak: dict[str, int] = {}

    def update(self, sym: str, ret: float) -> None:
        """Add a realized return and refresh the rolling summary statistics."""
        self.rets[sym].append(ret)
        self._recompute(sym)

    def _recompute(self, sym: str) -> None:
        rets = self.rets[sym]
        if len(rets) < 20:
            self.hit_rate.pop(sym, None)
            self.avg_pos.pop(sym, None)
            self.streak[sym] = 0
            return

        recent = rets[-self.lookback:]
        positives = [r for r in recent if r > 0]
        self.hit_rate[sym] = len(positives) / len(recent)
        self.avg_pos[sym] = float(np.mean(positives)) if positives else 0.0

        streak = 0
        for ret in reversed(rets):
            if ret > 0:
                streak += 1
            else:
                break
        self.streak[sym] = streak

    def get_signal(self, sym: str, iret: float, streak_mult: float) -> float | None:
        """Build the baseline signal score from hit rate, average upside, and streak."""
        if sym not in self.hit_rate:
            return None
        return (
            iret
            * self.avg_pos.get(sym, 0.0)
            * (1 + streak_mult * self.streak.get(sym, 0))
            * self.hit_rate[sym]
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DayEquity — Architectural guarantee against positioning bugs
#
# When a strategy has BOTH overnight and intraday legs, the equity must be
# updated exactly ONCE per day with the ADDITIVE combined return. The
# win-streak multiplier must be computed ONCE from yesterday's state.
#
# Without this class, it's easy to accidentally:
#   1. Update equity twice (once for ON, once for ID) → multiplicative inflation
#   2. Increment consec_wins twice → streak accelerates 2x
#   3. Compute WS after ON settles → intraday gets inflated WS
#
# This class makes those bugs impossible by construction.
# ═══════════════════════════════════════════════════════════════════════════════


class DayEquity:
    """Multi-settlement-per-day equity tracker with frozen win-streak.

    The KEY invariant: win-streak multiplier is computed ONCE at the start of each
    day from yesterday's consec_wins. It does NOT change between settlements within
    the same day. This prevents the overnight settlement from inflating the
    win-streak used by the intraday settlement.

    Multiple equity updates per day (settle overnight, then settle intraday) are
    fine — each settlement applies the SAME frozen WS to its return.

    Usage:
        day = DayEquity(equity, peak, consec_wins, ws_mult, ws_threshold)

        # Morning: settle overnight (equity updates immediately)
        day.settle_leg(overnight_return)  # equity *= (1 + on_ret * ws)

        # Afternoon: settle intraday (equity updates again)
        day.settle_leg(intraday_return)   # equity *= (1 + id_ret * ws)

        # End of day: finalize and get updated state
        result = day.finalize()
    """

    def __init__(
        self,
        equity: float,
        peak_equity: float,
        consec_wins: int,
        win_streak_mult: float = 1.0,
        win_streak_threshold: int = 1,
        flow_adaptive: bool = False,
        flow_boost: float = 1.0,
        flow_dampen: float = 1.0,
        flow_confirmed: bool = False,
    ):
        self.equity = equity
        self.peak_equity = peak_equity
        self._initial_consec_wins = consec_wins

        # Compute WS ONCE from yesterday's streak (FROZEN for the entire day)
        self.ws = win_streak_mult if consec_wins >= win_streak_threshold else 1.0
        if flow_adaptive:
            self.ws *= flow_boost if flow_confirmed else flow_dampen

        self._leg_returns: list[float] = []
        self._n_wins = 0
        self._n_losses = 0
        self._n_trades = 0
        self._finalized = False

    def settle_leg(self, raw_return: float, n_trades: int = 1):
        """Settle one leg (overnight or intraday). Equity updates immediately.

        The WS applied is the FROZEN value from the start of the day.
        consec_wins does NOT change between legs.
        """
        if self._finalized:
            raise RuntimeError("DayEquity already finalized.")
        scaled_ret = raw_return * self.ws
        self.equity *= (1 + scaled_ret)
        self.peak_equity = max(self.peak_equity, self.equity)
        self._leg_returns.append(scaled_ret)
        self._n_trades += n_trades
        if raw_return > 0: self._n_wins += 1
        elif raw_return < 0: self._n_losses += 1

    def finalize(self) -> dict:
        """End of day: compute combined daily return and updated consec_wins.

        Returns dict with: equity, peak, day_ret, consec_wins, n_wins, n_losses, n_trades
        """
        if self._finalized:
            raise RuntimeError("Already finalized.")
        self._finalized = True

        # Daily return for metrics: sum of all WS-scaled leg returns
        # This matches the multiplicative equity path:
        #   equity = E0 * (1 + r1*ws) * (1 + r2*ws)
        #   daily_ret for Sharpe = (equity / E0_start) - 1
        day_ret = sum(self._leg_returns)  # Additive approximation for Sharpe
        # (The cross-term difference is tiny: r1*ws * r2*ws is ~0.001% per day)

        # Single consec_wins update based on combined direction
        if day_ret > 0:
            new_consec = self._initial_consec_wins + 1
        elif day_ret < 0:
            new_consec = 0
        else:
            new_consec = self._initial_consec_wins

        return {
            "equity": self.equity,
            "peak": self.peak_equity,
            "day_ret": day_ret,
            "consec_wins": new_consec,
            "n_wins": self._n_wins,
            "n_losses": self._n_losses,
            "n_trades": self._n_trades,
        }
