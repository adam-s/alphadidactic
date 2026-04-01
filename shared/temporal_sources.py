"""Baseline-safe temporal source contracts for `causal_signal_research`.

This module introduces an additive source layer that sits *beside* the existing
baseline runners rather than replacing them. The goals are:

- keep `00_baseline` fully runnable without migration pressure
- make causal availability (`available_at_utc`) first-class
- provide a clean registry/adapter surface for future sources (options, news,
  SEC filings, derived flow, etc.)
- reuse the proven cursor engine and local baseline caches instead of copying
  buggy experiment implementations

The sources implemented here are only the ones we currently trust:

- checkpoint-scheduled minute-bar prices via `CursorEngine`
- conservative FRED snapshots from the local copied macro panel
- the local static universe used by the transplanted baseline
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Generic, Protocol, TypeVar
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2

from shared.config import DEFAULT_PUBLICATION_LAG, PUBLICATION_LAGS
from shared.cursor_engine import (
    AuditConfig,
    AuditLevel,
    CheckpointObservation,
    CheckpointSchedule,
    CursorEngine,
    MinuteBarsSource,
    ScheduleTape,
    faithful_exp17_schedule_17b,
)
from shared.research_core import get_symbols, load_fred_panel

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
REGULAR_OPEN_ET = time(9, 30)
REGULAR_CLOSE_ET = time(16, 0)

PayloadT = TypeVar("PayloadT")
RequestT = TypeVar("RequestT")


def _et_wall_time_to_utc(trade_date: date, wall_time: time) -> datetime:
    return datetime.combine(trade_date, wall_time, tzinfo=ET).astimezone(UTC)


def _parse_wall_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    hour_str, minute_str = value.split(":")
    return time(hour=int(hour_str), minute=int(minute_str))


def _classify_trade_size(size: int) -> str:
    if 1 <= size <= 9:
        return "retail"
    if 10 <= size <= 99:
        return "mid"
    return "institutional"


def _classify_dte_bucket(days_to_expiry: int) -> str:
    if days_to_expiry <= 0:
        return "0dte"
    if days_to_expiry == 1:
        return "next_day"
    if 2 <= days_to_expiry <= 3:
        return "dte_2_3"
    if 4 <= days_to_expiry <= 7:
        return "weekly"
    if 8 <= days_to_expiry <= 30:
        return "one_month"
    if 31 <= days_to_expiry <= 90:
        return "three_months"
    return "long_dated"


def _materialized_on_trade_date(tape: ScheduleTape) -> date:
    if not tape.checkpoint_trade_dates:
        return tape.anchor_trade_date
    return max(tape.checkpoint_trade_dates.values())


def _schedule_available_at_utc(tape: ScheduleTape) -> datetime:
    timestamps = [
        observation.ts_utc
        for checkpoint in tape.observations.values()
        for observation in checkpoint.values()
        if observation is not None
    ]
    if timestamps:
        return max(timestamps)
    return _et_wall_time_to_utc(_materialized_on_trade_date(tape), REGULAR_CLOSE_ET)


def _schedule_last_observation(tape: ScheduleTape) -> CheckpointObservation | None:
    observations = [
        observation
        for checkpoint in tape.observations.values()
        for observation in checkpoint.values()
        if observation is not None
    ]
    if not observations:
        return None
    return max(observations, key=lambda observation: observation.ts_utc)


def _fred_available_at_utc(observation_date: date, series_id: str) -> datetime:
    lag_days = PUBLICATION_LAGS.get(series_id, DEFAULT_PUBLICATION_LAG)
    available_date = (pd.Timestamp(observation_date) + pd.offsets.BDay(lag_days)).date()
    return _et_wall_time_to_utc(available_date, REGULAR_OPEN_ET)


@dataclass(frozen=True)
class SourceSnapshot(Generic[PayloadT]):
    source_key: str
    request_kind: str
    frontier_utc: datetime
    available_at_utc: datetime
    observed_at_utc: datetime | None
    payload: PayloadT
    provenance: str


@dataclass(frozen=True)
class PriceScheduleRequest:
    anchor_trade_date: date
    symbols: tuple[str, ...]
    schedule: CheckpointSchedule | None = None
    audit: bool = False


@dataclass(frozen=True)
class FredLatestRequest:
    as_of_trade_date: date
    series_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class FredObservation:
    series_id: str
    observation_date: date
    value: float
    available_at_utc: datetime


@dataclass(frozen=True)
class UniverseMembershipRequest:
    as_of_trade_date: date | None = None


@dataclass(frozen=True)
class OptionsWindowRequest:
    trade_date: date
    underlying: str
    start_et: str | time = REGULAR_OPEN_ET
    end_et: str | time = REGULAR_CLOSE_ET


@dataclass(frozen=True)
class FlowWindowRequest:
    trade_date: date
    underlying: str
    start_et: str | time = REGULAR_OPEN_ET
    end_et: str | time = REGULAR_CLOSE_ET


@dataclass(frozen=True)
class FlowWindowSummary:
    trade_date: date
    underlying: str
    start_et: str
    end_et: str
    trade_count: int
    total_contracts: int
    total_premium: float
    call_contracts: int
    put_contracts: int
    call_premium: float
    put_premium: float
    net_call_put_premium: float
    call_put_premium_ratio: float | None
    retail_premium: float
    mid_premium: float
    institutional_premium: float


@dataclass(frozen=True)
class FlowWindowSnapshot:
    summary: FlowWindowSummary
    breakdown: pd.DataFrame


@dataclass(frozen=True)
class FlowFeaturesRequest:
    trade_date: date
    underlying: str
    start_et: str | time = REGULAR_OPEN_ET
    end_et: str | time = REGULAR_CLOSE_ET


@dataclass(frozen=True)
class FlowFeaturesSnapshot:
    feature_row: dict[str, object]
    breakdown: pd.DataFrame


class TemporalSource(Protocol[RequestT, PayloadT]):
    key: str
    description: str

    def fetch(self, request: RequestT, **kwargs: object) -> SourceSnapshot[PayloadT]:
        ...


class MinuteBarScheduleSource:
    key = "prices.checkpoint_schedule"
    description = "Checkpoint-scheduled prices resolved causally from minute_bars via CursorEngine."

    def __init__(
        self,
        engine_factory: Callable[[], CursorEngine] | None = None,
    ) -> None:
        self._engine_factory = engine_factory or (
            lambda: CursorEngine(MinuteBarsSource(), faithful_exp17_schedule_17b())
        )

    def fetch(
        self,
        request: PriceScheduleRequest,
        **kwargs: object,
    ) -> SourceSnapshot[ScheduleTape]:
        engine = self._engine_factory()
        conn = kwargs.get("conn")
        owns_connection = conn is None
        db_conn = engine.source.get_connection() if conn is None else conn
        if db_conn is None:
            raise ValueError("Price schedule source requires a database connection")

        try:
            audit_level = AuditLevel.FULL if request.audit else AuditLevel.OFF
            tape = engine.resolve_schedule(
                db_conn,
                anchor_trade_date=request.anchor_trade_date,
                symbols=list(request.symbols),
                schedule=request.schedule or engine.default_schedule,
                audit=AuditConfig(level=audit_level, audit_dates={request.anchor_trade_date}),
            )
        finally:
            if owns_connection:
                db_conn.close()

        last_observation = _schedule_last_observation(tape)
        available_at_utc = _schedule_available_at_utc(tape)
        frontier_trade_date = _materialized_on_trade_date(tape)
        schedule_name = (request.schedule or engine.default_schedule).name
        return SourceSnapshot(
            source_key=self.key,
            request_kind="price_schedule",
            frontier_utc=_et_wall_time_to_utc(frontier_trade_date, REGULAR_CLOSE_ET),
            available_at_utc=available_at_utc,
            observed_at_utc=None if last_observation is None else last_observation.ts_utc,
            payload=tape,
            provenance=f"cursor_engine:{schedule_name}",
        )


class FredLatestSource:
    key = "macro.fred.latest"
    description = "Latest conservative FRED snapshot available by the open of a trade date."

    def __init__(self, panel_loader: Callable[[], pd.DataFrame] | None = None) -> None:
        self._panel_loader = panel_loader or load_fred_panel

    def fetch(
        self,
        request: FredLatestRequest,
        **kwargs: object,
    ) -> SourceSnapshot[dict[str, FredObservation | None]]:
        del kwargs
        panel = self._panel_loader()
        if not isinstance(panel.index, pd.DatetimeIndex):
            raise ValueError("FRED panel must use a DatetimeIndex")

        series_ids = tuple(sorted(panel.columns)) if request.series_ids is None else request.series_ids
        frontier_utc = _et_wall_time_to_utc(request.as_of_trade_date, REGULAR_OPEN_ET)

        snapshot: dict[str, FredObservation | None] = {}
        for series_id in series_ids:
            if series_id not in panel.columns:
                raise ValueError(f"Unknown FRED series: {series_id}")
            series = panel[series_id].dropna().sort_index()
            latest: FredObservation | None = None
            for observation_ts, value in series.items():
                observation_date = pd.Timestamp(observation_ts).date()
                available_at_utc = _fred_available_at_utc(observation_date, series_id)
                if available_at_utc > frontier_utc:
                    continue
                latest = FredObservation(
                    series_id=series_id,
                    observation_date=observation_date,
                    value=float(value),
                    available_at_utc=available_at_utc,
                )
            snapshot[series_id] = latest

        available_times = [
            observation.available_at_utc
            for observation in snapshot.values()
            if observation is not None
        ]
        return SourceSnapshot(
            source_key=self.key,
            request_kind="fred_latest",
            frontier_utc=frontier_utc,
            available_at_utc=max(available_times) if available_times else frontier_utc,
            observed_at_utc=None,
            payload=snapshot,
            provenance="shared/cache/msvar_panel.parquet",
        )


class StaticUniverseSource:
    key = "universe.static"
    description = "Static baseline universe copied into the self-contained research tree."

    def __init__(self, symbol_loader: Callable[[], Sequence[str]] | None = None) -> None:
        self._symbol_loader = symbol_loader or get_symbols

    def fetch(
        self,
        request: UniverseMembershipRequest,
        **kwargs: object,
    ) -> SourceSnapshot[tuple[str, ...]]:
        del kwargs
        frontier_date = request.as_of_trade_date or date(1970, 1, 1)
        symbols = tuple(sorted(self._symbol_loader()))
        return SourceSnapshot(
            source_key=self.key,
            request_kind="static_universe",
            frontier_utc=_et_wall_time_to_utc(frontier_date, REGULAR_OPEN_ET),
            available_at_utc=datetime(1970, 1, 1, tzinfo=UTC),
            observed_at_utc=None,
            payload=symbols,
            provenance="shared/cache/intraday_prices.parquet",
        )


class OptionsTradesSource:
    key = "options.raw_window"
    description = "Single-day options_trades window fetched with explicit ET->UTC bounds."

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or MinuteBarsSource().db_url

    def _get_connection(self) -> psycopg2.extensions.connection:
        return psycopg2.connect(self._db_url)

    def fetch(
        self,
        request: OptionsWindowRequest,
        **kwargs: object,
    ) -> SourceSnapshot[pd.DataFrame]:
        start_time = _parse_wall_time(request.start_et)
        end_time = _parse_wall_time(request.end_et)
        start_utc = _et_wall_time_to_utc(request.trade_date, start_time)
        end_utc = _et_wall_time_to_utc(request.trade_date, end_time)
        if end_utc <= start_utc:
            raise ValueError("Options window end must be after start")

        conn = kwargs.get("conn")
        owns_connection = conn is None
        db_conn = self._get_connection() if conn is None else conn
        if db_conn is None:
            raise ValueError("Options source requires a database connection")

        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT sip_timestamp, option_type, strike, price, size, expiration
                    FROM options_trades
                    WHERE underlying = %s
                      AND sip_timestamp >= %s::timestamptz
                      AND sip_timestamp < %s::timestamptz
                    ORDER BY sip_timestamp
                    """,
                    (
                        request.underlying,
                        start_utc.strftime("%Y-%m-%d %H:%M:%S+00"),
                        end_utc.strftime("%Y-%m-%d %H:%M:%S+00"),
                    ),
                )
                rows = cur.fetchall()
        finally:
            if owns_connection:
                db_conn.close()

        frame = pd.DataFrame(
            rows,
            columns=["sip_timestamp", "option_type", "strike", "price", "size", "expiration"],
        )
        if frame.empty:
            observed_at_utc = None
            payload = pd.DataFrame(
                columns=[
                    "sip_timestamp",
                    "option_type",
                    "strike",
                    "price",
                    "size",
                    "expiration",
                    "trade_date_et",
                    "minute_et",
                    "premium",
                    "size_class",
                ]
            )
        else:
            sip_ts = pd.to_datetime(frame["sip_timestamp"], utc=True)
            trade_date_et = sip_ts.dt.tz_convert(ET).dt.date
            if not bool((trade_date_et == request.trade_date).all()):
                raise ValueError(
                    f"Options source returned rows outside requested ET trade date {request.trade_date}"
                )
            minute_et = sip_ts.dt.tz_convert(ET).dt.strftime("%H:%M")
            premium = frame["price"].astype(float) * frame["size"].astype(int) * 100.0
            size_class = frame["size"].astype(int).map(_classify_trade_size)
            payload = frame.assign(
                trade_date_et=trade_date_et,
                minute_et=minute_et,
                premium=premium,
                size_class=size_class,
            )
            observed_at_utc = sip_ts.max().to_pydatetime()

        return SourceSnapshot(
            source_key=self.key,
            request_kind="options_window",
            frontier_utc=end_utc,
            available_at_utc=end_utc,
            observed_at_utc=observed_at_utc,
            payload=payload,
            provenance="options_trades:single_day_window",
        )


    def fetch_batch(
        self,
        trade_date: date,
        underlyings: list[str] | tuple[str, ...],
        *,
        start_et: str | time = REGULAR_OPEN_ET,
        end_et: str | time = REGULAR_CLOSE_ET,
        conn: psycopg2.extensions.connection | None = None,
    ) -> dict[str, SourceSnapshot[pd.DataFrame]]:
        """Fetch options trades for multiple underlyings in a single SQL query.

        Returns a dict mapping each underlying to its SourceSnapshot.
        Symbols with no trades get an empty-DataFrame snapshot.
        """
        start_time = _parse_wall_time(start_et)
        end_time = _parse_wall_time(end_et)
        start_utc = _et_wall_time_to_utc(trade_date, start_time)
        end_utc = _et_wall_time_to_utc(trade_date, end_time)
        if end_utc <= start_utc:
            raise ValueError("Options window end must be after start")

        owns_connection = conn is None
        db_conn = self._get_connection() if conn is None else conn

        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT underlying, sip_timestamp, option_type, strike, price, size, expiration
                    FROM options_trades
                    WHERE underlying = ANY(%s)
                      AND sip_timestamp >= %s::timestamptz
                      AND sip_timestamp < %s::timestamptz
                    ORDER BY underlying, sip_timestamp
                    """,
                    (
                        list(underlyings),
                        start_utc.strftime("%Y-%m-%d %H:%M:%S+00"),
                        end_utc.strftime("%Y-%m-%d %H:%M:%S+00"),
                    ),
                )
                rows = cur.fetchall()
        finally:
            if owns_connection:
                db_conn.close()

        empty_cols = [
            "sip_timestamp", "option_type", "strike", "price", "size",
            "expiration", "trade_date_et", "minute_et", "premium", "size_class",
        ]
        empty_payload = pd.DataFrame(columns=empty_cols)

        if not rows:
            return {
                sym: SourceSnapshot(
                    source_key=self.key, request_kind="options_window",
                    frontier_utc=end_utc, available_at_utc=end_utc,
                    observed_at_utc=None, payload=empty_payload.copy(),
                    provenance="options_trades:batch_window",
                )
                for sym in underlyings
            }

        all_frame = pd.DataFrame(
            rows, columns=["underlying", "sip_timestamp", "option_type",
                           "strike", "price", "size", "expiration"],
        )
        sip_ts = pd.to_datetime(all_frame["sip_timestamp"], utc=True)
        trade_date_et = sip_ts.dt.tz_convert(ET).dt.date
        minute_et = sip_ts.dt.tz_convert(ET).dt.strftime("%H:%M")
        premium = all_frame["price"].astype(float) * all_frame["size"].astype(int) * 100.0
        size_class = all_frame["size"].astype(int).map(_classify_trade_size)
        all_frame = all_frame.assign(
            trade_date_et=trade_date_et, minute_et=minute_et,
            premium=premium, size_class=size_class,
        )

        result: dict[str, SourceSnapshot[pd.DataFrame]] = {}
        for sym in underlyings:
            sym_frame = all_frame[all_frame["underlying"] == sym].drop(columns=["underlying"])
            if sym_frame.empty:
                result[sym] = SourceSnapshot(
                    source_key=self.key, request_kind="options_window",
                    frontier_utc=end_utc, available_at_utc=end_utc,
                    observed_at_utc=None, payload=empty_payload.copy(),
                    provenance="options_trades:batch_window",
                )
            else:
                observed_at = pd.to_datetime(sym_frame["sip_timestamp"], utc=True).max().to_pydatetime()
                result[sym] = SourceSnapshot(
                    source_key=self.key, request_kind="options_window",
                    frontier_utc=end_utc, available_at_utc=end_utc,
                    observed_at_utc=observed_at, payload=sym_frame.reset_index(drop=True),
                    provenance="options_trades:batch_window",
                )
        return result


class SimpleFlowSource:
    key = "flow.simple_window"
    description = "Derived premium-flow summary built from single-day raw options trades."

    def __init__(self, raw_source: OptionsTradesSource | None = None) -> None:
        self._raw_source = raw_source or OptionsTradesSource()

    def fetch(
        self,
        request: FlowWindowRequest,
        **kwargs: object,
    ) -> SourceSnapshot[FlowWindowSnapshot]:
        provided_raw_snapshot = kwargs.pop("raw_snapshot", None)
        raw_snapshot = (
            provided_raw_snapshot
            if isinstance(provided_raw_snapshot, SourceSnapshot)
            else self._raw_source.fetch(
                OptionsWindowRequest(
                    trade_date=request.trade_date,
                    underlying=request.underlying,
                    start_et=request.start_et,
                    end_et=request.end_et,
                ),
                **kwargs,
            )
        )
        trades = raw_snapshot.payload.copy()

        summary, breakdown = _build_simple_flow_payload(
            trades=trades,
            trade_date=request.trade_date,
            underlying=request.underlying,
            start_et=request.start_et,
            end_et=request.end_et,
        )

        payload = FlowWindowSnapshot(summary=summary, breakdown=breakdown)
        return SourceSnapshot(
            source_key=self.key,
            request_kind="simple_flow_window",
            frontier_utc=raw_snapshot.frontier_utc,
            available_at_utc=raw_snapshot.available_at_utc,
            observed_at_utc=raw_snapshot.observed_at_utc,
            payload=payload,
            provenance=f"{raw_snapshot.provenance}->simple_premium_flow",
        )


def _build_simple_flow_payload(
    *,
    trades: pd.DataFrame,
    trade_date: date,
    underlying: str,
    start_et: str | time,
    end_et: str | time,
) -> tuple[FlowWindowSummary, pd.DataFrame]:
    start_label = _parse_wall_time(start_et).strftime("%H:%M")
    end_label = _parse_wall_time(end_et).strftime("%H:%M")
    if trades.empty:
        breakdown = pd.DataFrame(
            columns=["option_type", "size_class", "trade_count", "total_contracts", "total_premium"]
        )
        summary = FlowWindowSummary(
            trade_date=trade_date,
            underlying=underlying,
            start_et=start_label,
            end_et=end_label,
            trade_count=0,
            total_contracts=0,
            total_premium=0.0,
            call_contracts=0,
            put_contracts=0,
            call_premium=0.0,
            put_premium=0.0,
            net_call_put_premium=0.0,
            call_put_premium_ratio=None,
            retail_premium=0.0,
            mid_premium=0.0,
            institutional_premium=0.0,
        )
        return summary, breakdown

    trades = trades.copy()
    trades["size"] = trades["size"].astype(int)
    trades["premium"] = trades["premium"].astype(float)
    grouped = (
        trades.groupby(["option_type", "size_class"], as_index=False)
        .agg(
            trade_count=("option_type", "size"),
            total_contracts=("size", "sum"),
            total_premium=("premium", "sum"),
        )
        .sort_values(["option_type", "size_class"])
        .reset_index(drop=True)
    )
    call_mask = trades["option_type"] == "call"
    put_mask = trades["option_type"] == "put"
    call_premium = float(trades.loc[call_mask, "premium"].sum())
    put_premium = float(trades.loc[put_mask, "premium"].sum())
    call_contracts = int(trades.loc[call_mask, "size"].sum())
    put_contracts = int(trades.loc[put_mask, "size"].sum())
    summary = FlowWindowSummary(
        trade_date=trade_date,
        underlying=underlying,
        start_et=start_label,
        end_et=end_label,
        trade_count=int(len(trades)),
        total_contracts=int(trades["size"].sum()),
        total_premium=float(trades["premium"].sum()),
        call_contracts=call_contracts,
        put_contracts=put_contracts,
        call_premium=call_premium,
        put_premium=put_premium,
        net_call_put_premium=call_premium - put_premium,
        call_put_premium_ratio=None if put_premium == 0 else call_premium / put_premium,
        retail_premium=float(trades.loc[trades["size_class"] == "retail", "premium"].sum()),
        mid_premium=float(trades.loc[trades["size_class"] == "mid", "premium"].sum()),
        institutional_premium=float(trades.loc[trades["size_class"] == "institutional", "premium"].sum()),
    )
    return summary, grouped


class CanonicalFlowFeaturesSource:
    key = "flow.features_window.v1"
    description = "Canonical DTE-aware flow feature row for future experiments, built from single-day raw options trades."

    def __init__(self, raw_source: OptionsTradesSource | None = None) -> None:
        self._raw_source = raw_source or OptionsTradesSource()

    def fetch(
        self,
        request: FlowFeaturesRequest,
        **kwargs: object,
    ) -> SourceSnapshot[FlowFeaturesSnapshot]:
        provided_raw_snapshot = kwargs.pop("raw_snapshot", None)
        raw_snapshot = (
            provided_raw_snapshot
            if isinstance(provided_raw_snapshot, SourceSnapshot)
            else self._raw_source.fetch(
                OptionsWindowRequest(
                    trade_date=request.trade_date,
                    underlying=request.underlying,
                    start_et=request.start_et,
                    end_et=request.end_et,
                ),
                **kwargs,
            )
        )
        trades = raw_snapshot.payload.copy()
        breakdown = _build_feature_breakdown(trades=trades, trade_date=request.trade_date)
        feature_row = _build_feature_row(
            breakdown=breakdown,
            trade_date=request.trade_date,
            underlying=request.underlying,
            start_et=request.start_et,
            end_et=request.end_et,
            raw_trade_count=int(len(trades)),
        )
        payload = FlowFeaturesSnapshot(feature_row=feature_row, breakdown=breakdown)
        return SourceSnapshot(
            source_key=self.key,
            request_kind="flow_features_window_v1",
            frontier_utc=raw_snapshot.frontier_utc,
            available_at_utc=raw_snapshot.available_at_utc,
            observed_at_utc=raw_snapshot.observed_at_utc,
            payload=payload,
            provenance=f"{raw_snapshot.provenance}->canonical_flow_features_v1",
        )


def _build_feature_breakdown(*, trades: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=[
                "option_type",
                "size_class",
                "dte_bucket",
                "trade_count",
                "total_contracts",
                "total_premium",
            ]
        )
    work = trades.copy()
    work["size"] = work["size"].astype(int)
    work["premium"] = work["premium"].astype(float)
    expiration_dates = pd.to_datetime(work["expiration"]).dt.date
    work["days_to_expiry"] = [(expiration_date - trade_date).days for expiration_date in expiration_dates]
    work["dte_bucket"] = work["days_to_expiry"].map(_classify_dte_bucket)
    return (
        work.groupby(["option_type", "size_class", "dte_bucket"], as_index=False)
        .agg(
            trade_count=("option_type", "size"),
            total_contracts=("size", "sum"),
            total_premium=("premium", "sum"),
        )
        .sort_values(["option_type", "size_class", "dte_bucket"])
        .reset_index(drop=True)
    )


def _sum_breakdown(
    breakdown: pd.DataFrame,
    *,
    option_type: str | None = None,
    size_class: str | None = None,
    dte_bucket: str | None = None,
    column: str = "total_premium",
) -> float:
    if breakdown.empty:
        return 0.0
    mask = pd.Series(True, index=breakdown.index)
    if option_type is not None:
        mask &= breakdown["option_type"] == option_type
    if size_class is not None:
        mask &= breakdown["size_class"] == size_class
    if dte_bucket is not None:
        mask &= breakdown["dte_bucket"] == dte_bucket
    return float(breakdown.loc[mask, column].sum())


def _build_feature_row(
    *,
    breakdown: pd.DataFrame,
    trade_date: date,
    underlying: str,
    start_et: str | time,
    end_et: str | time,
    raw_trade_count: int,
) -> dict[str, object]:
    start_label = _parse_wall_time(start_et).strftime("%H:%M")
    end_label = _parse_wall_time(end_et).strftime("%H:%M")
    feature_row: dict[str, object] = {
        "trade_date": pd.Timestamp(trade_date),
        "underlying": underlying,
        "window_start_et": start_label,
        "window_end_et": end_label,
        "raw_trade_count": raw_trade_count,
        "total_premium": _sum_breakdown(breakdown),
        "call_premium": _sum_breakdown(breakdown, option_type="call"),
        "put_premium": _sum_breakdown(breakdown, option_type="put"),
        "total_contracts": _sum_breakdown(breakdown, column="total_contracts"),
        "call_contracts": _sum_breakdown(breakdown, option_type="call", column="total_contracts"),
        "put_contracts": _sum_breakdown(breakdown, option_type="put", column="total_contracts"),
    }
    put_premium = float(feature_row["put_premium"])
    call_premium = float(feature_row["call_premium"])
    feature_row["net_call_put_premium"] = call_premium - put_premium
    feature_row["call_put_premium_ratio"] = None if put_premium == 0 else call_premium / put_premium

    for size_class in ("retail", "mid", "institutional"):
        feature_row[f"{size_class}_premium"] = _sum_breakdown(breakdown, size_class=size_class)
        feature_row[f"{size_class}_contracts"] = _sum_breakdown(
            breakdown, size_class=size_class, column="total_contracts"
        )
        for option_type in ("call", "put"):
            feature_row[f"{option_type}_{size_class}_premium"] = _sum_breakdown(
                breakdown,
                option_type=option_type,
                size_class=size_class,
            )

    for dte_bucket in (
        "0dte",
        "next_day",
        "dte_2_3",
        "weekly",
        "one_month",
        "three_months",
        "long_dated",
    ):
        feature_row[f"premium_{dte_bucket}"] = _sum_breakdown(breakdown, dte_bucket=dte_bucket)
        feature_row[f"contracts_{dte_bucket}"] = _sum_breakdown(
            breakdown,
            dte_bucket=dte_bucket,
            column="total_contracts",
        )
        feature_row[f"call_put_net_{dte_bucket}"] = _sum_breakdown(
            breakdown,
            option_type="call",
            dte_bucket=dte_bucket,
        ) - _sum_breakdown(
            breakdown,
            option_type="put",
            dte_bucket=dte_bucket,
        )
    return feature_row


@dataclass(frozen=True)
class EarningsWindowRequest:
    trade_date: date
    symbols: tuple[str, ...] | None = None


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    release_date: date
    eps_actual: float | None
    eps_forecast: float | None
    surprise_pct: float | None
    reporting_time: str | None
    available_at_utc: datetime


class EarningsReleasesSource:
    key = "earnings.releases"
    description = "Single-day earnings releases with explicit availability based on reporting_time (BMO/AMC)."

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or MinuteBarsSource().db_url

    def _get_connection(self) -> psycopg2.extensions.connection:
        return psycopg2.connect(self._db_url)

    def fetch(
        self,
        request: EarningsWindowRequest,
        **kwargs: object,
    ) -> SourceSnapshot[list[EarningsEvent]]:
        conn = kwargs.get("conn")
        owns_connection = conn is None
        db_conn = self._get_connection() if conn is None else conn
        if db_conn is None:
            raise ValueError("Earnings source requires a database connection")

        try:
            with db_conn.cursor() as cur:
                if request.symbols is not None:
                    cur.execute(
                        """
                        SELECT ticker, release_date,
                               eps_actual_numeric, eps_forecast_numeric,
                               eps_surprise, reporting_time
                        FROM earnings_releases
                        WHERE release_date = %s
                          AND ticker = ANY(%s)
                        ORDER BY ticker
                        """,
                        (request.trade_date.isoformat(), list(request.symbols)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT ticker, release_date,
                               eps_actual_numeric, eps_forecast_numeric,
                               eps_surprise, reporting_time
                        FROM earnings_releases
                        WHERE release_date = %s
                        ORDER BY ticker
                        """,
                        (request.trade_date.isoformat(),),
                    )
                rows = cur.fetchall()
        finally:
            if owns_connection:
                db_conn.close()

        events: list[EarningsEvent] = []
        for ticker, rel_date, eps_actual, eps_forecast, surprise, rpt_time in rows:
            rpt_str = str(rpt_time).strip().lower() if rpt_time else None
            if rpt_str == "bmo":
                available = _et_wall_time_to_utc(rel_date, time(9, 30))
            else:
                available = _et_wall_time_to_utc(rel_date, time(16, 0))
            events.append(EarningsEvent(
                symbol=str(ticker),
                release_date=rel_date,
                eps_actual=float(eps_actual) if eps_actual is not None else None,
                eps_forecast=float(eps_forecast) if eps_forecast is not None else None,
                surprise_pct=float(surprise) if surprise is not None else None,
                reporting_time=rpt_str,
                available_at_utc=available,
            ))

        frontier = _et_wall_time_to_utc(request.trade_date, REGULAR_CLOSE_ET)
        return SourceSnapshot(
            source_key=self.key,
            request_kind="earnings_releases",
            frontier_utc=frontier,
            available_at_utc=frontier,
            observed_at_utc=None,
            payload=events,
            provenance="earnings_releases:single_day",
        )


class TemporalSourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, TemporalSource[object, object]] = {}

    def register(self, source: TemporalSource[object, object]) -> None:
        if source.key in self._sources:
            raise ValueError(f"Source already registered: {source.key}")
        self._sources[source.key] = source

    def get(self, key: str) -> TemporalSource[object, object]:
        if key not in self._sources:
            known_sources = ", ".join(sorted(self._sources))
            raise KeyError(f"Unknown source {key!r}. Known sources: {known_sources}")
        return self._sources[key]

    def list_sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._sources))


def build_default_registry() -> TemporalSourceRegistry:
    registry = TemporalSourceRegistry()
    registry.register(MinuteBarScheduleSource())
    registry.register(FredLatestSource())
    registry.register(StaticUniverseSource())
    registry.register(OptionsTradesSource())
    registry.register(SimpleFlowSource())
    registry.register(CanonicalFlowFeaturesSource())
    registry.register(EarningsReleasesSource())
    return registry