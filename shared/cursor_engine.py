"""Causal checkpoint resolution utilities for self-contained signal research.

This module resolves same-day and next-trading-day checkpoint prices directly
from `minute_bars` while enforcing trading-day and timezone correctness.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time as clock_time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

import psycopg2

DEFAULT_DB_URL = os.environ.get("DATABASE_URL", "")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
REGULAR_OPEN_ET = clock_time(9, 30)
REGULAR_CLOSE_ET = clock_time(16, 0)


class CursorEngineError(Exception):
    """Base exception for cursor engine runtime failures."""


class DataIntegrityError(CursorEngineError):
    """Raised when fetched market data violates engine assumptions."""


class CalendarResolutionError(CursorEngineError):
    """Raised when trading-day resolution fails for a schedule."""


class MissingCheckpointError(CursorEngineError):
    """Raised when a required checkpoint cannot be resolved."""


class ResolutionMode(str, Enum):
    AT_OR_BEFORE = "at_or_before"
    AT_OR_AFTER = "at_or_after"
    NEAREST_WITHIN_SAME_DAY = "nearest_within_same_day"
    BEFORE_THEN_AFTER = "before_then_after"


class AuditLevel(str, Enum):
    OFF = "off"
    ERRORS = "errors"
    SAMPLED = "sampled"
    FULL = "full"


@dataclass(frozen=True)
class Checkpoint:
    name: str
    target_time_et: clock_time
    mode: ResolutionMode
    grace_minutes_before: int = 0
    grace_minutes_after: int = 0
    required: bool = True
    session: str = "regular"
    trading_day_offset: int = 0


@dataclass(frozen=True)
class CheckpointSchedule:
    name: str
    checkpoints: tuple[Checkpoint, ...]

    def __post_init__(self) -> None:
        names = [checkpoint.name for checkpoint in self.checkpoints]
        if len(names) != len(set(names)):
            raise ValueError(f"CheckpointSchedule {self.name} has duplicate checkpoint names: {names}")

    @property
    def trading_day_offsets(self) -> tuple[int, ...]:
        return tuple(sorted({checkpoint.trading_day_offset for checkpoint in self.checkpoints}))


@dataclass(frozen=True)
class CandidateBar:
    symbol: str
    ts_utc: datetime
    close: float

    @property
    def ts_et(self) -> datetime:
        return self.ts_utc.replace(tzinfo=UTC).astimezone(ET)


@dataclass(frozen=True)
class CheckpointObservation:
    symbol: str
    checkpoint: str
    anchor_trade_date: date
    trade_date: date
    price: float
    ts_utc: datetime
    ts_et: datetime
    resolution: str


@dataclass(frozen=True)
class AuditConfig:
    level: AuditLevel = AuditLevel.OFF
    audit_symbols: set[str] = field(default_factory=set)
    audit_dates: set[date] = field(default_factory=set)

    def should_emit(self, trade_date: date, symbol: str, failure: bool = False) -> bool:
        if self.level == AuditLevel.OFF:
            return False
        if self.level == AuditLevel.ERRORS:
            return failure
        if self.level == AuditLevel.FULL:
            return True
        date_ok = not self.audit_dates or trade_date in self.audit_dates
        symbol_ok = not self.audit_symbols or symbol in self.audit_symbols
        return date_ok and symbol_ok


@dataclass(frozen=True)
class AuditRecord:
    trade_date: date
    anchor_trade_date: date
    symbol: str
    checkpoint: str
    target_time_et: str
    mode: str
    lower_bound_utc: str
    upper_bound_utc: str
    candidate_times_utc: list[str]
    chosen_time_utc: str | None
    chosen_price: float | None
    resolution: str
    rejection_reason: str | None = None


@dataclass(frozen=True)
class DayTape:
    trade_date: date
    observations: dict[str, dict[str, CheckpointObservation | None]]
    audit_records: list[AuditRecord]

    def get_price(self, checkpoint: str, symbol: str) -> float | None:
        obs = self.observations.get(checkpoint, {}).get(symbol)
        return None if obs is None else obs.price


@dataclass(frozen=True)
class ScheduleTape:
    anchor_trade_date: date
    checkpoint_trade_dates: dict[str, date]
    observations: dict[str, dict[str, CheckpointObservation | None]]
    audit_records: list[AuditRecord]

    def get_price(self, checkpoint: str, symbol: str) -> float | None:
        obs = self.observations.get(checkpoint, {}).get(symbol)
        return None if obs is None else obs.price

    def as_day_tape(self) -> DayTape:
        resolved_dates = set(self.checkpoint_trade_dates.values())
        if len(resolved_dates) > 1:
            raise ValueError("Cannot coerce multi-day schedule tape into a DayTape")
        trade_date = next(iter(resolved_dates), self.anchor_trade_date)
        return DayTape(
            trade_date=trade_date,
            observations=self.observations,
            audit_records=self.audit_records,
        )


class MinuteBarsSource:
    def __init__(self, db_url: str = DEFAULT_DB_URL):
        self.db_url = db_url

    def get_connection(self) -> psycopg2.extensions.connection:
        return psycopg2.connect(self.db_url)

    def get_trading_days(
        self,
        conn: psycopg2.extensions.connection,
        start: str,
        end: str,
        anchor_symbol: str = "SPY",
    ) -> list[date]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT
                    (((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::date)
                    AS trade_date
                FROM minute_bars
                WHERE symbol = %s
                  AND (((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time)
                      >= '09:30:00'
                  AND (((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time)
                      <= '16:00:00'
                  AND time >= %s::timestamp
                  AND time < %s::timestamp
                ORDER BY trade_date
                """,
                (anchor_symbol, f"{start} 00:00:00", f"{end} 23:59:59"),
            )
            return [row[0] for row in cur.fetchall()]

    def fetch_day_candidates(
        self,
        conn: psycopg2.extensions.connection,
        trade_date: date,
        symbols: list[str],
        checkpoints: list[Checkpoint],
    ) -> dict[str, list[CandidateBar]]:
        if not checkpoints:
            return {symbol: [] for symbol in symbols}

        lower_dt = min(_checkpoint_lower_bound_utc(trade_date, checkpoint) for checkpoint in checkpoints)
        upper_dt = max(_checkpoint_upper_bound_utc(trade_date, checkpoint) for checkpoint in checkpoints)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol, time, close
                FROM minute_bars
                WHERE symbol = ANY(%s)
                  AND time >= %s::timestamp
                  AND time <= %s::timestamp
                ORDER BY symbol, time
                """,
                (symbols, _utc_ts(lower_dt), _utc_ts(upper_dt)),
            )
            rows = cur.fetchall()

        out: dict[str, list[CandidateBar]] = {symbol: [] for symbol in symbols}
        for symbol, ts, close in rows:
            if ts is None or close is None:
                continue
            candidate = CandidateBar(symbol=symbol, ts_utc=ts, close=float(close))
            ts_et = candidate.ts_et
            ts_time_et = ts_et.timetz().replace(tzinfo=None)
            if ts_et.date() != trade_date:
                raise DataIntegrityError(
                    f"Timezone violation: {symbol} row {ts} UTC maps to {ts_et.date()} ET, expected {trade_date}"
                )
            if ts_time_et < REGULAR_OPEN_ET or ts_time_et > REGULAR_CLOSE_ET:
                continue  # skip pre/post-market bars (data quality issue)
            out[symbol].append(candidate)
        return out


def _et_to_utc(trade_date: date, time_et: clock_time) -> datetime:
    return datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        time_et.hour,
        time_et.minute,
        tzinfo=ET,
    ).astimezone(UTC).replace(tzinfo=None)


def _et_datetime_to_utc(dt_et: datetime) -> datetime:
    if dt_et.tzinfo is None:
        dt_et = dt_et.replace(tzinfo=ET)
    else:
        dt_et = dt_et.astimezone(ET)
    return dt_et.astimezone(UTC).replace(tzinfo=None)


def _utc_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _checkpoint_lower_bound_utc(trade_date: date, checkpoint: Checkpoint) -> datetime:
    dt = datetime.combine(trade_date, checkpoint.target_time_et, tzinfo=ET)
    dt -= timedelta(minutes=checkpoint.grace_minutes_before)
    return _et_datetime_to_utc(dt)


def _checkpoint_upper_bound_utc(trade_date: date, checkpoint: Checkpoint) -> datetime:
    dt = datetime.combine(trade_date, checkpoint.target_time_et, tzinfo=ET)
    dt += timedelta(minutes=checkpoint.grace_minutes_after)
    return _et_datetime_to_utc(dt)


class CursorEngine:
    def __init__(self, source: MinuteBarsSource, checkpoints: CheckpointSchedule | list[Checkpoint]):
        self.source = source
        if isinstance(checkpoints, CheckpointSchedule):
            self.default_schedule = checkpoints
        else:
            self.default_schedule = build_schedule("default", checkpoints)
        self.checkpoints = list(self.default_schedule.checkpoints)

    def resolve_day(
        self,
        conn: psycopg2.extensions.connection,
        trade_date: date,
        symbols: list[str],
        audit: AuditConfig | None = None,
    ) -> DayTape:
        if any(checkpoint.trading_day_offset != 0 for checkpoint in self.default_schedule.checkpoints):
            raise CalendarResolutionError("resolve_day only supports schedules with trading_day_offset == 0")
        return self.resolve_schedule(
            conn,
            anchor_trade_date=trade_date,
            symbols=symbols,
            schedule=self.default_schedule,
            audit=audit,
        ).as_day_tape()

    def resolve_schedule(
        self,
        conn: psycopg2.extensions.connection,
        anchor_trade_date: date,
        symbols: list[str],
        schedule: CheckpointSchedule | None = None,
        trading_days: list[date] | None = None,
        audit: AuditConfig | None = None,
    ) -> ScheduleTape:
        schedule_def = schedule or self.default_schedule
        audit_cfg = audit or AuditConfig()
        checkpoint_trade_dates = self._resolve_checkpoint_trade_dates(
            conn,
            anchor_trade_date=anchor_trade_date,
            schedule=schedule_def,
            trading_days=trading_days,
        )
        observations: dict[str, dict[str, CheckpointObservation | None]] = {
            checkpoint.name: {} for checkpoint in schedule_def.checkpoints
        }
        audit_records: list[AuditRecord] = []

        candidates_by_trade_date = {
            trade_date: self.source.fetch_day_candidates(
                conn,
                trade_date,
                symbols,
                [
                    checkpoint
                    for checkpoint in schedule_def.checkpoints
                    if checkpoint_trade_dates[checkpoint.name] == trade_date
                ],
            )
            for trade_date in sorted(set(checkpoint_trade_dates.values()))
        }

        for checkpoint in schedule_def.checkpoints:
            checkpoint_trade_date = checkpoint_trade_dates[checkpoint.name]
            lower_dt = _checkpoint_lower_bound_utc(checkpoint_trade_date, checkpoint)
            upper_dt = _checkpoint_upper_bound_utc(checkpoint_trade_date, checkpoint)
            target_dt = _et_to_utc(checkpoint_trade_date, checkpoint.target_time_et)
            candidates_by_symbol = candidates_by_trade_date[checkpoint_trade_date]

            for symbol in symbols:
                symbol_candidates = [
                    candidate for candidate in candidates_by_symbol.get(symbol, [])
                    if lower_dt <= candidate.ts_utc <= upper_dt
                ]
                chosen, resolution, reason = _resolve_checkpoint(symbol_candidates, checkpoint, target_dt)

                if chosen is None and checkpoint.required:
                    reason = reason or "required checkpoint missing"

                if chosen is None:
                    observations[checkpoint.name][symbol] = None
                else:
                    observations[checkpoint.name][symbol] = CheckpointObservation(
                        symbol=symbol,
                        checkpoint=checkpoint.name,
                        anchor_trade_date=anchor_trade_date,
                        trade_date=checkpoint_trade_date,
                        price=chosen.close,
                        ts_utc=chosen.ts_utc,
                        ts_et=chosen.ts_et,
                        resolution=resolution,
                    )

                failure = chosen is None and checkpoint.required
                if audit_cfg.should_emit(anchor_trade_date, symbol, failure=failure):
                    audit_records.append(
                        AuditRecord(
                            trade_date=checkpoint_trade_date,
                            anchor_trade_date=anchor_trade_date,
                            symbol=symbol,
                            checkpoint=checkpoint.name,
                            target_time_et=checkpoint.target_time_et.strftime("%H:%M"),
                            mode=checkpoint.mode.value,
                            lower_bound_utc=_utc_ts(lower_dt),
                            upper_bound_utc=_utc_ts(upper_dt),
                            candidate_times_utc=[_utc_ts(candidate.ts_utc) for candidate in symbol_candidates],
                            chosen_time_utc=None if chosen is None else _utc_ts(chosen.ts_utc),
                            chosen_price=None if chosen is None else chosen.close,
                            resolution=resolution,
                            rejection_reason=reason,
                        )
                    )

                if failure:
                    raise MissingCheckpointError(
                        f"Missing required checkpoint {checkpoint.name} for {symbol} on anchor {anchor_trade_date} (resolved {checkpoint_trade_date}): {reason}"
                    )

        return ScheduleTape(
            anchor_trade_date=anchor_trade_date,
            checkpoint_trade_dates=checkpoint_trade_dates,
            observations=observations,
            audit_records=audit_records,
        )

    def _resolve_checkpoint_trade_dates(
        self,
        conn: psycopg2.extensions.connection,
        *,
        anchor_trade_date: date,
        schedule: CheckpointSchedule,
        trading_days: list[date] | None,
    ) -> dict[str, date]:
        if not schedule.checkpoints:
            return {}

        if all(checkpoint.trading_day_offset == 0 for checkpoint in schedule.checkpoints):
            return {checkpoint.name: anchor_trade_date for checkpoint in schedule.checkpoints}

        calendar = list(trading_days) if trading_days is not None else self._load_local_trading_calendar(
            conn,
            anchor_trade_date,
            max_abs_offset=max(abs(offset) for offset in schedule.trading_day_offsets),
        )
        if anchor_trade_date not in calendar:
            raise CalendarResolutionError(
                f"Anchor trade_date {anchor_trade_date} is not present in trading calendar"
            )

        anchor_idx = calendar.index(anchor_trade_date)
        out: dict[str, date] = {}
        for checkpoint in schedule.checkpoints:
            resolved_idx = anchor_idx + checkpoint.trading_day_offset
            if resolved_idx < 0 or resolved_idx >= len(calendar):
                raise CalendarResolutionError(
                    f"Checkpoint {checkpoint.name} offset {checkpoint.trading_day_offset} is out of trading-calendar bounds for {anchor_trade_date}"
                )
            out[checkpoint.name] = calendar[resolved_idx]
        return out

    def _load_local_trading_calendar(
        self,
        conn: psycopg2.extensions.connection,
        anchor_trade_date: date,
        max_abs_offset: int,
    ) -> list[date]:
        # US-equity-specific padding heuristic: enough to step across weekends/market holidays
        # for the small offsets used by the schedules in this module.
        padding_days = max(7, max_abs_offset * 7 + 7)
        start = (anchor_trade_date - timedelta(days=padding_days)).isoformat()
        end = (anchor_trade_date + timedelta(days=padding_days)).isoformat()
        return self.source.get_trading_days(conn, start, end)


def _resolve_checkpoint(
    candidates: list[CandidateBar],
    checkpoint: Checkpoint,
    target_dt: datetime,
) -> tuple[CandidateBar | None, str, str | None]:
    if not candidates:
        return None, "missing", "no same-day candidates in checkpoint window"

    if checkpoint.mode == ResolutionMode.AT_OR_BEFORE:
        eligible = [candidate for candidate in candidates if candidate.ts_utc <= target_dt]
        if not eligible:
            return None, "missing", "no same-day candidate at or before target"
        chosen = max(eligible, key=lambda candidate: candidate.ts_utc)
        resolution = "exact" if chosen.ts_utc == target_dt else "same_day_carry"
        return chosen, resolution, None

    if checkpoint.mode == ResolutionMode.AT_OR_AFTER:
        eligible = [candidate for candidate in candidates if candidate.ts_utc >= target_dt]
        if not eligible:
            return None, "missing", "no same-day candidate at or after target"
        chosen = min(eligible, key=lambda candidate: candidate.ts_utc)
        resolution = "exact" if chosen.ts_utc == target_dt else "same_day_wait"
        return chosen, resolution, None

    if checkpoint.mode == ResolutionMode.BEFORE_THEN_AFTER:
        # Step 1: try earlier (known prices, causally safe)
        earlier = [candidate for candidate in candidates if candidate.ts_utc <= target_dt]
        if earlier:
            chosen = max(earlier, key=lambda candidate: candidate.ts_utc)
            resolution = "exact" if chosen.ts_utc == target_dt else "same_day_carry"
            return chosen, resolution, None
        # Step 2: nothing earlier — try later (next executable price)
        later = [candidate for candidate in candidates if candidate.ts_utc > target_dt]
        if later:
            chosen = min(later, key=lambda candidate: candidate.ts_utc)
            return chosen, "same_day_wait_fallback", None
        return None, "missing", "no same-day candidate in either direction"

    # Tie-break equal-distance candidates toward the earlier timestamp for determinism.
    chosen = min(
        candidates,
        key=lambda candidate: (abs((candidate.ts_utc - target_dt).total_seconds()), candidate.ts_utc),
    )
    resolution = "exact" if chosen.ts_utc == target_dt else "nearest_within_same_day"
    return chosen, resolution, None


def build_schedule(name: str, checkpoints: list[Checkpoint] | tuple[Checkpoint, ...]) -> CheckpointSchedule:
    return CheckpointSchedule(name=name, checkpoints=tuple(checkpoints))


# ═══════════════════════════════════════════════════════════════════════════════
# Per-experiment price cache (flat resolved prices, like deep-research)
# ═══════════════════════════════════════════════════════════════════════════════


def build_price_cache(
    engine: CursorEngine,
    conn,
    trading_days: list[date],
    symbols: list[str],
    cache_path: "Path",
):
    """Build flat resolved-price parquet from DB via CursorEngine.

    One row per (date, symbol). One column per checkpoint name. Prices are
    fully resolved (grace windows, half-day fallbacks applied). NaN = no price.

    This runs the full CursorEngine resolution for every day — same code path
    as the strategy loop. The cache is a pre-computed snapshot of what
    PhasedDay.resolve_up_to() would return.
    """
    import sys
    import pandas as pd
    from tqdm import tqdm

    checkpoint_names = [cp.name for cp in engine.default_schedule.checkpoints]
    rows = []

    for today in tqdm(trading_days, desc="Building price cache", file=sys.stderr):
        try:
            tape = engine.resolve_day(conn, today, symbols)
        except MissingCheckpointError:
            continue  # expected: some days may lack required checkpoints
        except Exception as e:
            print(f"  WARNING: cache build failed for {today}: {e}", file=sys.stderr)
            raise  # unexpected errors must not be silently swallowed
        for sym in symbols:
            row = {"date": today, "symbol": sym}
            for cp_name in checkpoint_names:
                row[cp_name] = tape.get_price(cp_name, sym)
            rows.append(row)

    df = pd.DataFrame(rows)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    print(f"  Cache built: {cache_path} ({len(df)} rows, {len(checkpoint_names)} checkpoints)",
          file=sys.stderr)
    return df


def load_price_cache(cache_path: "Path") -> dict[date, dict[str, dict[str, float | None]]]:
    """Load flat cache into nested dict for O(1) lookup.

    Returns: {trade_date: {symbol: {checkpoint_name: price_or_None}}}
    """
    import pandas as pd

    df = pd.read_parquet(cache_path)
    checkpoint_cols = [c for c in df.columns if c not in ("date", "symbol")]
    cache: dict[date, dict[str, dict[str, float | None]]] = {}

    dates = df["date"].values
    symbols = df["symbol"].values
    price_arrays = {col: df[col].values for col in checkpoint_cols}

    for i in range(len(df)):
        td = dates[i]
        if hasattr(td, "date"):
            td = td.date() if callable(getattr(td, "date", None)) else td
        elif hasattr(td, "astype"):
            td = pd.Timestamp(td).date()
        sym = str(symbols[i])

        if td not in cache:
            cache[td] = {}
        sym_prices = {}
        for col in checkpoint_cols:
            v = price_arrays[col][i]
            sym_prices[col] = None if pd.isna(v) else float(v)
        cache[td][sym] = sym_prices

    return cache


class CachedPhasedDay:
    """Drop-in replacement for PhasedDay that reads from flat cache.

    Same interface: resolve_up_to(frontier_time) returns {cp_name: {sym: price}}.
    Used when price_cache.parquet exists. Falls back to DB via PhasedDay when not.
    """

    def __init__(self, cache_data: dict, today: date, schedule: CheckpointSchedule):
        self._data = cache_data.get(today, {})
        self._schedule = schedule
        self._resolved: dict[str, dict[str, float | None]] = {}

    def resolve_up_to(self, frontier_time_et: clock_time) -> dict[str, dict[str, float | None]]:
        new = {}
        for cp in self._schedule.checkpoints:
            if cp.target_time_et <= frontier_time_et and cp.name not in self._resolved:
                prices = {}
                for sym, sym_prices in self._data.items():
                    prices[sym] = sym_prices.get(cp.name)
                self._resolved[cp.name] = prices
                new[cp.name] = prices
        return new


def settle_price_fallback(
    engine: "CursorEngine",
    conn,
    symbol: str,
    trade_date: date,
    target_time_et: str = "09:35",
) -> tuple[float | None, str | None, str]:
    """Settlement fallback: search full trading day for the best available price.

    When the exact target bar is missing, this fetches all bars for the symbol
    on that day in one cheap query and finds the best price locally.

    The data collector typically skips bars when price is unchanged, so an
    earlier same-day price is usually the current price for equities and ETFs.
    This does NOT apply to options (which can expire worthless) or to multi-day
    gaps (which may indicate real data issues). The caller must review all
    fallback resolutions logged in output/data_gaps.json.

    Search order:
        1. Most recent bar BEFORE target time on same day (price likely unchanged)
        2. Earliest bar AFTER target time on same day (next available trade)
        3. None if no bars exist for the entire day

    Returns:
        (price, resolved_time_et, resolution_type) where resolution_type is one of:
        - "same_day_carry": earlier bar used (price was unchanged)
        - "same_day_forward": later bar used (no earlier data)
        - "no_price": no bars on this trading day
    """
    target_h = int(target_time_et.split(":")[0])
    target_m = int(target_time_et.split(":")[1])
    target_minutes = target_h * 60 + target_m

    # One cheap query: all bars for this symbol on this day
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                close,
                ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time AS et_time
            FROM minute_bars
            WHERE symbol = %s
              AND time >= %s::timestamp
              AND time < %s::timestamp
              AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
                  BETWEEN '09:30:00' AND '16:00:00'
            ORDER BY time
        """, (symbol, f"{trade_date} 00:00:00", f"{trade_date} 23:59:59"))
        rows = cur.fetchall()

    if not rows:
        return None, None, "no_price"

    # Search earlier first: most recent bar before target time
    before = [(price, et) for price, et in rows
              if et.hour * 60 + et.minute <= target_minutes]
    if before:
        price, et = before[-1]  # rows are ordered by time, last = most recent
        return float(price), f"{et.hour:02d}:{et.minute:02d}", "same_day_carry"

    # Nothing earlier: earliest bar after target time
    after = [(price, et) for price, et in rows
             if et.hour * 60 + et.minute > target_minutes]
    if after:
        price, et = after[0]  # first = earliest
        return float(price), f"{et.hour:02d}:{et.minute:02d}", "same_day_forward"

    return None, None, "no_price"


def regular_session_checkpoints_17b() -> list[Checkpoint]:
    return [
        Checkpoint(
            name="p0935",
            target_time_et=clock_time(9, 35),
            mode=ResolutionMode.AT_OR_BEFORE,
            grace_minutes_before=5,
            grace_minutes_after=0,
            required=False,
        ),
        Checkpoint(
            name="p1600",
            target_time_et=clock_time(16, 0),
            mode=ResolutionMode.AT_OR_BEFORE,
            grace_minutes_before=390,  # R5: covers half-day closes at 13:00 ET
            grace_minutes_after=0,
            required=False,
        ),
    ]


def faithful_exp17_schedule_17b() -> CheckpointSchedule:
    return build_schedule("exp17_faithful", regular_session_checkpoints_17b())


def faithful_exp17_causal_schedule_17b() -> CheckpointSchedule:
    return build_schedule(
        "exp17_faithful_causal",
        [
            Checkpoint(
                name="open_0935",
                target_time_et=clock_time(9, 35),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=5,
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="close_1600",
                target_time_et=clock_time(16, 0),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=390,  # R5: covers half-day closes at 13:00 ET
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
        ],
    )


def same_day_intraday_decomposition_schedule_17b() -> CheckpointSchedule:
    return build_schedule(
        "same_day_intraday_decomposition",
        [
            Checkpoint(
                name="open_0935",
                target_time_et=clock_time(9, 35),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=5,
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="mid_1200",
                target_time_et=clock_time(12, 0),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=180,  # R5: covers half-day closes at 13:00 ET
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="close_1600",
                target_time_et=clock_time(16, 0),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=390,  # R5: covers half-day closes at 13:00 ET
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
        ],
    )


def afternoon_entry_next_open_schedule_17b() -> CheckpointSchedule:
    return build_schedule(
        "afternoon_entry_next_open",
        [
            Checkpoint(
                name="entry_1600",
                target_time_et=clock_time(16, 0),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=390,  # R5: covers half-day closes at 13:00 ET
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="exit_next_0935",
                target_time_et=clock_time(9, 35),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=5,
                grace_minutes_after=0,
                required=False,
                trading_day_offset=1,
            ),
        ],
    )


def intraday_overnight_decomposition_schedule_17b() -> CheckpointSchedule:
    return build_schedule(
        "intraday_overnight_decomposition",
        [
            Checkpoint(
                name="open_0935",
                target_time_et=clock_time(9, 35),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=5,
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="mid_1200",
                target_time_et=clock_time(12, 0),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=180,  # R5: covers half-day closes at 13:00 ET
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="close_1600",
                target_time_et=clock_time(16, 0),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=390,  # R5: covers half-day closes at 13:00 ET
                grace_minutes_after=0,
                required=False,
                trading_day_offset=0,
            ),
            Checkpoint(
                name="next_open_0935",
                target_time_et=clock_time(9, 35),
                mode=ResolutionMode.AT_OR_BEFORE,
                grace_minutes_before=5,
                grace_minutes_after=0,
                required=False,
                trading_day_offset=1,
            ),
        ],
    )


def day_tape_to_legacy_price_map(
    day_tape: DayTape | ScheduleTape,
    symbols: list[str],
) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    for checkpoint, by_symbol in day_tape.observations.items():
        out[checkpoint] = {}
        for symbol in symbols:
            observation = by_symbol.get(symbol)
            out[checkpoint][symbol] = None if observation is None else observation.price
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# PHASED RESOLUTION — Architectural guarantee against intraday look-ahead
#
# When a strategy has BOTH intraday and overnight trades, the morning decision
# (9:35) must not see close prices (16:00). PhasedDay enforces this by resolving
# checkpoints in time order. The 16:00 data physically does not exist in memory
# until Phase 2 is explicitly requested.
#
# This is slower (two DB queries per day instead of one). Honesty > speed.
# ═══════════════════════════════════════════════════════════════════════════════


class PhasedDay:
    """Resolves checkpoints in temporal phases so future data cannot leak.

    Usage:
        phased = PhasedDay(engine, conn, today, symbols, schedule, trading_days)

        # Phase 1: Only checkpoints at or before 9:35 are available
        p0935 = phased.resolve_up_to(clock_time(9, 35))
        # p0935 = {"p0935": {"SPY": 500.0, ...}}
        # p1600 does NOT EXIST — cannot be accessed

        make_morning_decisions(p0935)

        # Phase 2: Now close checkpoints become available too
        p1600 = phased.resolve_up_to(clock_time(16, 0))
        # p1600 = {"p1600": {"SPY": 502.0, ...}}
        # p0935 still accessible via phased.prices["p0935"]

        make_close_decisions(phased.prices)
    """

    def __init__(
        self,
        engine: CursorEngine,
        conn,
        trade_date: date,
        symbols: list[str],
        schedule: CheckpointSchedule | None = None,
        trading_days: list[date] | None = None,
    ):
        self.engine = engine
        self.conn = conn
        self.trade_date = trade_date
        self.symbols = symbols
        self.schedule = schedule or engine.default_schedule
        self.trading_days = trading_days

        # Track which checkpoints have been resolved
        self._resolved_checkpoints: set[str] = set()
        self._frontier_et: clock_time | None = None

        # Accumulated prices across phases
        self.prices: dict[str, dict[str, float | None]] = {}

    def resolve_up_to(self, frontier_time_et: clock_time) -> dict[str, dict[str, float | None]]:
        """Resolve all checkpoints with target_time_et <= frontier_time_et.

        Returns ONLY the newly resolved checkpoints (not previously resolved ones).
        All resolved prices are also accumulated in self.prices.

        The DB query is bounded to only fetch bars up to the frontier time,
        so close-time bars are physically never loaded during morning resolution.
        """
        if self._frontier_et is not None and frontier_time_et < self._frontier_et:
            raise CursorEngineError(
                f"PhasedDay frontier cannot move backward: {self._frontier_et} -> {frontier_time_et}"
            )
        self._frontier_et = frontier_time_et

        # Find checkpoints to resolve in this phase
        new_checkpoints = [
            cp for cp in self.schedule.checkpoints
            if cp.name not in self._resolved_checkpoints
            and cp.target_time_et <= frontier_time_et
            and cp.trading_day_offset == 0  # Same-day only for phased resolution
        ]

        if not new_checkpoints:
            return {}

        # Build a sub-schedule with only the new checkpoints
        phase_schedule = build_schedule(
            f"{self.schedule.name}_phase_{frontier_time_et.strftime('%H%M')}",
            new_checkpoints,
        )

        # Resolve — the DB query is naturally bounded because the checkpoints
        # define the time window. fetch_day_candidates uses the checkpoint bounds
        # to compute lower/upper UTC, so only bars within those bounds are fetched.
        tape = self.engine.resolve_schedule(
            self.conn,
            anchor_trade_date=self.trade_date,
            symbols=self.symbols,
            schedule=phase_schedule,
            trading_days=self.trading_days,
        )

        # Extract prices for newly resolved checkpoints
        new_prices: dict[str, dict[str, float | None]] = {}
        for cp in new_checkpoints:
            self._resolved_checkpoints.add(cp.name)
            cp_prices: dict[str, float | None] = {}
            for symbol in self.symbols:
                obs = tape.observations.get(cp.name, {}).get(symbol)
                cp_prices[symbol] = None if obs is None else obs.price
            new_prices[cp.name] = cp_prices
            self.prices[cp.name] = cp_prices

        return new_prices

    def get(self, checkpoint: str) -> dict[str, float | None]:
        """Get prices for a resolved checkpoint. Raises if not yet resolved."""
        if checkpoint not in self._resolved_checkpoints:
            raise CursorEngineError(
                f"Checkpoint '{checkpoint}' has not been resolved yet. "
                f"Current frontier: {self._frontier_et}. "
                f"Resolved: {sorted(self._resolved_checkpoints)}. "
                f"Call resolve_up_to() with a later frontier first."
            )
        return self.prices.get(checkpoint, {})

    @property
    def frontier(self) -> clock_time | None:
        """The current wall-clock frontier. Data beyond this does not exist."""
        return self._frontier_et
