from __future__ import annotations

from datetime import date
from typing import Iterable

from shared.cursor_engine import AuditConfig, AuditLevel, CheckpointSchedule, CursorEngine, MinuteBarsSource, ScheduleTape
from shared.run_cache import RunScopedObservationCache


def select_trading_days(
    *,
    anchor_symbol: str,
    start_date: str,
    end_date: str,
    min_days: int,
    max_days: int | None = None,
    source: MinuteBarsSource | None = None,
) -> list[date]:
    minute_source = source or MinuteBarsSource()
    conn = minute_source.get_connection()
    try:
        trading_days = minute_source.get_trading_days(conn, start_date, end_date, anchor_symbol=anchor_symbol)
    finally:
        conn.close()
    if len(trading_days) < min_days:
        raise AssertionError(f"Need at least {min_days} trading days")
    return trading_days if max_days is None else trading_days[: max(max_days, min_days)]


def resolve_schedule_tape(
    *,
    engine: CursorEngine,
    conn,
    anchor_trade_date: date,
    current_frontier: date,
    symbols: list[str],
    schedule: CheckpointSchedule | None = None,
    trading_days: list[date] | None = None,
    observation_cache: RunScopedObservationCache | None = None,
    audit: bool = False,
    audit_symbols: Iterable[str] | None = None,
) -> ScheduleTape:
    audit_symbol_set = set(audit_symbols or ())

    def resolver() -> ScheduleTape:
        return engine.resolve_schedule(
            conn,
            anchor_trade_date=anchor_trade_date,
            symbols=symbols,
            schedule=schedule,
            trading_days=trading_days,
            audit=AuditConfig(
                level=AuditLevel.FULL if audit else AuditLevel.OFF,
                audit_dates={anchor_trade_date},
                audit_symbols=audit_symbol_set,
            ),
        )

    if observation_cache is None:
        return resolver()
    return observation_cache.get_or_compute(
        anchor_trade_date=anchor_trade_date,
        current_frontier=current_frontier,
        resolver=resolver,
    )
