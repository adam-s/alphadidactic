"""Split-aware return normalization utilities for self-contained signal research.

These helpers keep raw price lookup causal while applying only the corporate
actions that would have been knowable by the current frontier trade date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import psycopg2


@dataclass(frozen=True)
class SplitEvent:
    symbol: str
    effective_trade_date: date
    split_ratio: float
    source: str = "fallback"


DEFAULT_SPLIT_EVENTS: tuple[SplitEvent, ...] = (
    SplitEvent("AMZN", date(2022, 6, 6), 20.0),
    SplitEvent("GOOG", date(2022, 7, 18), 20.0),
    SplitEvent("GOOGL", date(2022, 7, 18), 20.0),
    SplitEvent("GME", date(2022, 7, 22), 4.0),
    SplitEvent("TSLA", date(2022, 8, 25), 3.0),
    SplitEvent("SHOP", date(2022, 6, 29), 10.0),
    SplitEvent("PANW", date(2022, 9, 14), 3.0),
    SplitEvent("AMC", date(2023, 8, 24), 0.1),
    SplitEvent("UNG", date(2024, 1, 24), 0.25),
    SplitEvent("WMT", date(2024, 2, 26), 3.0),
    SplitEvent("QID", date(2024, 4, 10), 0.2),
    SplitEvent("NVDA", date(2024, 6, 10), 10.0),
    SplitEvent("VXX", date(2023, 3, 7), 0.25),
    SplitEvent("VXX", date(2024, 7, 24), 0.25),
    SplitEvent("AVGO", date(2024, 7, 15), 10.0),
    SplitEvent("MSTR", date(2024, 8, 8), 10.0),
    SplitEvent("SMCI", date(2024, 10, 1), 10.0),
    SplitEvent("SQQQ", date(2022, 1, 13), 0.2),
    SplitEvent("SQQQ", date(2024, 11, 7), 0.2),
    SplitEvent("SQQQ", date(2025, 11, 20), 0.2),
    SplitEvent("SDS", date(2022, 1, 13), 0.2),
    SplitEvent("SDS", date(2025, 11, 20), 0.2),
    SplitEvent("TQQQ", date(2022, 1, 13), 2.0),
    SplitEvent("TQQQ", date(2025, 11, 20), 2.0),
    SplitEvent("SSO", date(2022, 1, 13), 2.0),
    SplitEvent("SSO", date(2025, 11, 20), 2.0),
    SplitEvent("QLD", date(2025, 11, 20), 2.0),
    SplitEvent("SPXS", date(2025, 9, 29), 0.1),
)


class CorporateActionLedger:
    def __init__(self, split_events: Iterable[SplitEvent]):
        deduped: dict[tuple[str, date], SplitEvent] = {}
        for event in split_events:
            deduped[(event.symbol, event.effective_trade_date)] = event
        self._by_symbol: dict[str, tuple[SplitEvent, ...]] = {}
        for event in sorted(
            deduped.values(),
            key=lambda value: (value.symbol, value.effective_trade_date, value.split_ratio),
        ):
            self._by_symbol.setdefault(event.symbol, tuple())
        grouped: dict[str, list[SplitEvent]] = {}
        for event in sorted(
            deduped.values(),
            key=lambda value: (value.symbol, value.effective_trade_date, value.split_ratio),
        ):
            grouped.setdefault(event.symbol, []).append(event)
        self._by_symbol = {symbol: tuple(events) for symbol, events in grouped.items()}

    def events_known_by(
        self,
        symbol: str,
        frontier_trade_date: date,
    ) -> tuple[SplitEvent, ...]:
        return tuple(
            event
            for event in self._by_symbol.get(symbol, ())
            if event.effective_trade_date <= frontier_trade_date
        )

    def split_factor_between(
        self,
        symbol: str,
        start_trade_date: date,
        end_trade_date: date,
        frontier_trade_date: date,
    ) -> float:
        if end_trade_date <= start_trade_date:
            return 1.0
        cutoff = min(end_trade_date, frontier_trade_date)
        factor = 1.0
        for event in self.events_known_by(symbol, cutoff):
            if start_trade_date < event.effective_trade_date <= end_trade_date:
                factor *= event.split_ratio
        return factor


def load_split_events_from_db(
    conn: psycopg2.extensions.connection,
    symbols: list[str],
    start_trade_date: date,
    end_trade_date: date,
) -> list[SplitEvent]:
    if not symbols:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, split_date, split_ratio::float
            FROM stock_splits
            WHERE symbol = ANY(%s)
              AND split_date >= %s
              AND split_date <= %s
              AND split_ratio != 1.0
            ORDER BY symbol, split_date
            """,
            (symbols, start_trade_date.isoformat(), end_trade_date.isoformat()),
        )
        rows = cur.fetchall()

    return [
        SplitEvent(
            symbol=str(symbol),
            effective_trade_date=split_date,
            split_ratio=float(split_ratio),
            source="db",
        )
        for symbol, split_date, split_ratio in rows
    ]


def build_default_split_ledger(
    *,
    conn: psycopg2.extensions.connection | None,
    symbols: list[str],
    start_trade_date: date,
    end_trade_date: date,
) -> CorporateActionLedger:
    symbol_set = set(symbols)
    events = [event for event in DEFAULT_SPLIT_EVENTS if event.symbol in symbol_set]
    if conn is not None and symbols:
        try:
            db_events = load_split_events_from_db(conn, symbols, start_trade_date, end_trade_date)
            events.extend(db_events)
        except psycopg2.Error:
            conn.rollback()
    return CorporateActionLedger(events)


def calculate_split_aware_return(
    *,
    symbol: str,
    entry_price_raw: float | None,
    entry_trade_date: date,
    exit_price_raw: float | None,
    exit_trade_date: date,
    frontier_trade_date: date,
    ledger: CorporateActionLedger,
) -> float | None:
    if (
        entry_price_raw is None
        or exit_price_raw is None
        or entry_price_raw <= 0
        or exit_price_raw <= 0
    ):
        return None
    factor = ledger.split_factor_between(
        symbol=symbol,
        start_trade_date=entry_trade_date,
        end_trade_date=exit_trade_date,
        frontier_trade_date=frontier_trade_date,
    )
    return exit_price_raw * factor / entry_price_raw - 1


def calculate_split_aware_overnight_return(
    *,
    symbol: str,
    prior_close_raw: float | None,
    prior_close_trade_date: date,
    next_open_raw: float | None,
    next_open_trade_date: date,
    frontier_trade_date: date,
    ledger: CorporateActionLedger,
) -> float | None:
    return calculate_split_aware_return(
        symbol=symbol,
        entry_price_raw=prior_close_raw,
        entry_trade_date=prior_close_trade_date,
        exit_price_raw=next_open_raw,
        exit_trade_date=next_open_trade_date,
        frontier_trade_date=frontier_trade_date,
        ledger=ledger,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C-exit safe return helper
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ReturnResult:
    """Return value that forces callers to handle C-exit cases explicitly.

    Statuses:
        normal           — valid return computed from both prices
        non_trade        — entry price missing, no position was entered (0%)
        max_loss         — entry exists but exit missing or invalid (-100%)
        split_neutralized — return exceeded split_threshold, neutralized to 0%
    """
    value: float
    status: str


def settle_return(
    *,
    symbol: str,
    entry_price: float | None,
    entry_trade_date: date,
    exit_price: float | None,
    exit_trade_date: date,
    frontier_trade_date: date,
    ledger: CorporateActionLedger,
    split_threshold: float | None = None,
) -> ReturnResult:
    """Compute a return with explicit C-exit handling.

    Unlike calculate_split_aware_return (which returns None for any missing
    price), this function distinguishes missing-entry (non-trade, 0%) from
    missing-exit (max loss, -100%). This prevents the most common accounting
    bug in the codebase (C-exit, 8 cycles of recurrence).

    Args:
        symbol: Instrument symbol
        entry_price: Entry price (None = no position entered)
        entry_trade_date: Trade date of entry
        exit_price: Exit price (None = can't exit → max loss)
        exit_trade_date: Trade date of exit
        frontier_trade_date: Point-in-time boundary for split knowledge
        ledger: Corporate action ledger
        split_threshold: If set, returns with abs(ret) > threshold are
            neutralized to 0% with status "split_neutralized"

    Returns:
        ReturnResult with .value and .status
    """
    # Case 1: No entry price → non-trade (no position was entered)
    if entry_price is None:
        return ReturnResult(0.0, "non_trade")

    # Case 2: Entry exists but exit missing → max loss
    if exit_price is None:
        return ReturnResult(-1.0, "max_loss")

    # Case 3: Both prices present → compute split-aware return
    ret = calculate_split_aware_return(
        symbol=symbol,
        entry_price_raw=entry_price,
        entry_trade_date=entry_trade_date,
        exit_price_raw=exit_price,
        exit_trade_date=exit_trade_date,
        frontier_trade_date=frontier_trade_date,
        ledger=ledger,
    )

    # calculate_split_aware_return returns None on validation failure
    # (e.g., price <= 0). Treat as max loss — we entered but can't compute exit.
    if ret is None:
        return ReturnResult(-1.0, "max_loss")

    # Optional: split detection via magnitude threshold
    if split_threshold is not None and abs(ret) > split_threshold:
        return ReturnResult(0.0, "split_neutralized")

    return ReturnResult(ret, "normal")