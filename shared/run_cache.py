"""Run-scoped observation cache for causal schedule tapes.

The cache is append-only and frontier-aware so cached observations cannot leak
future information back into an earlier simulation step.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path

from shared.cursor_engine import CheckpointObservation, CheckpointSchedule, ScheduleTape

CACHE_VERSION = 1


class RunCacheMode(str, Enum):
    OFF = "off"
    MEMORY = "memory"
    DISK = "disk"


@dataclass(frozen=True)
class RunCacheManifest:
    cache_version: int
    kind: str
    run_id: str
    run_label: str
    schedule_name: str
    schedule_signature: str
    symbol_hash: str
    symbol_count: int
    created_at_utc: str


def normalize_cache_mode(value: str | RunCacheMode) -> RunCacheMode:
    if isinstance(value, RunCacheMode):
        return value
    return RunCacheMode(value)


class RunScopedObservationCache:
    def __init__(
        self,
        *,
        mode: str | RunCacheMode,
        base_dir: Path,
        schedule: CheckpointSchedule,
        symbols: list[str],
        run_label: str,
        run_id: str | None = None,
    ):
        self.mode = normalize_cache_mode(mode)
        self.base_dir = base_dir
        self.schedule = schedule
        self.symbols = tuple(symbols)
        self.run_label = run_label
        self.run_id = run_id or _generated_run_id(run_label)
        self.schedule_signature = _schedule_signature(schedule)
        self.symbol_hash = _symbols_hash(self.symbols)
        self.frontier_trade_date: date | None = None
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self._memory_cache: dict[date, ScheduleTape] = {}
        self.run_dir = self.base_dir / self.run_id
        self.observations_dir = self.run_dir / "observations"
        self.manifest_path = self.run_dir / "manifest.json"

        if self.mode == RunCacheMode.DISK:
            self._initialize_disk_layout()

    def get_or_compute(
        self,
        *,
        anchor_trade_date: date,
        current_frontier: date,
        resolver,
    ) -> ScheduleTape:
        cached = self.get(anchor_trade_date=anchor_trade_date, current_frontier=current_frontier)
        if cached is not None:
            return cached
        resolved = resolver()
        return self.put(tape=resolved, current_frontier=current_frontier)

    def get(
        self,
        *,
        anchor_trade_date: date,
        current_frontier: date,
    ) -> ScheduleTape | None:
        self._advance_frontier(current_frontier)
        if anchor_trade_date > current_frontier:
            raise AssertionError(
                f"Run cache read requested future anchor {anchor_trade_date} with frontier {current_frontier}"
            )
        if self.mode == RunCacheMode.OFF:
            return None

        cached = self._memory_cache.get(anchor_trade_date)
        if cached is not None:
            self._assert_read_safe(cached, current_frontier)
            self.hits += 1
            return cached

        if self.mode == RunCacheMode.DISK:
            cached = self._load_from_disk(anchor_trade_date)
            if cached is not None:
                self._assert_read_safe(cached, current_frontier)
                self._memory_cache[anchor_trade_date] = cached
                self.hits += 1
                return cached

        self.misses += 1
        return None

    def put(self, *, tape: ScheduleTape, current_frontier: date) -> ScheduleTape:
        self._advance_frontier(current_frontier)
        self._assert_write_safe(tape, current_frontier)
        existing = self._memory_cache.get(tape.anchor_trade_date)
        if existing is None and self.mode == RunCacheMode.DISK:
            existing = self._load_from_disk(tape.anchor_trade_date)
        if existing is not None:
            if _serialize_schedule_tape(existing) != _serialize_schedule_tape(tape):
                raise AssertionError(
                    f"Append-only cache violation for {tape.anchor_trade_date}: existing cached tape differs from new tape"
                )
            self._memory_cache[tape.anchor_trade_date] = existing
            return existing

        if self.mode != RunCacheMode.OFF:
            self._memory_cache[tape.anchor_trade_date] = tape
            if self.mode == RunCacheMode.DISK:
                self._write_to_disk(tape)
            self.writes += 1
        return tape

    def stats(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "run_id": self.run_id,
            "run_dir": None if self.mode != RunCacheMode.DISK else str(self.run_dir),
            "frontier_trade_date": None
            if self.frontier_trade_date is None
            else self.frontier_trade_date.isoformat(),
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
        }

    def _advance_frontier(self, current_frontier: date) -> None:
        if self.frontier_trade_date is None:
            self.frontier_trade_date = current_frontier
            return
        if current_frontier < self.frontier_trade_date:
            raise AssertionError(
                f"Run cache frontier moved backwards from {self.frontier_trade_date} to {current_frontier}"
            )
        self.frontier_trade_date = current_frontier

    def _assert_read_safe(self, tape: ScheduleTape, current_frontier: date) -> None:
        if tape.anchor_trade_date > current_frontier:
            raise AssertionError(
                f"Run cache read would leak future anchor {tape.anchor_trade_date} at frontier {current_frontier}"
            )
        materialized_on = _materialized_on_trade_date(tape)
        if materialized_on > current_frontier:
            raise AssertionError(
                "Run cache read would expose checkpoint values before they became knowable: "
                f"materialized_on={materialized_on}, frontier={current_frontier}, anchor={tape.anchor_trade_date}"
            )

    def _assert_write_safe(self, tape: ScheduleTape, current_frontier: date) -> None:
        if tape.anchor_trade_date > current_frontier:
            raise AssertionError(
                f"Run cache write attempted for future anchor {tape.anchor_trade_date} with frontier {current_frontier}"
            )
        materialized_on = _materialized_on_trade_date(tape)
        if materialized_on > current_frontier:
            raise AssertionError(
                "Run cache write attempted before all checkpoint values became knowable: "
                f"materialized_on={materialized_on}, frontier={current_frontier}, anchor={tape.anchor_trade_date}"
            )

    def _initialize_disk_layout(self) -> None:
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        manifest = RunCacheManifest(
            cache_version=CACHE_VERSION,
            kind="schedule_observation_cache",
            run_id=self.run_id,
            run_label=self.run_label,
            schedule_name=self.schedule.name,
            schedule_signature=self.schedule_signature,
            symbol_hash=self.symbol_hash,
            symbol_count=len(self.symbols),
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        if self.manifest_path.exists():
            persisted = json.loads(self.manifest_path.read_text())
            expected = asdict(manifest)
            expected["created_at_utc"] = persisted.get("created_at_utc", "")
            for key, value in expected.items():
                if persisted.get(key) != value:
                    raise AssertionError(
                        f"Run cache manifest mismatch for {self.run_id}: field {key} expected {value!r}, found {persisted.get(key)!r}"
                    )
            return
        self.manifest_path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True))

    def _load_from_disk(self, anchor_trade_date: date) -> ScheduleTape | None:
        path = self._tape_path(anchor_trade_date)
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        return _deserialize_schedule_tape(payload)

    def _write_to_disk(self, tape: ScheduleTape) -> None:
        path = self._tape_path(tape.anchor_trade_date)
        payload = _serialize_schedule_tape(tape)
        if path.exists():
            persisted = json.loads(path.read_text())
            if persisted != payload:
                raise AssertionError(
                    f"Append-only disk cache violation for {tape.anchor_trade_date}: existing file differs"
                )
            return
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    def _tape_path(self, anchor_trade_date: date) -> Path:
        return self.observations_dir / f"{anchor_trade_date.isoformat()}.json"


def _generated_run_id(run_label: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{run_label}_{timestamp}"


def _materialized_on_trade_date(tape: ScheduleTape) -> date:
    if not tape.checkpoint_trade_dates:
        return tape.anchor_trade_date
    return max(tape.checkpoint_trade_dates.values())


def _schedule_signature(schedule: CheckpointSchedule) -> str:
    payload = {
        "name": schedule.name,
        "checkpoints": [
            {
                "name": checkpoint.name,
                "target_time_et": checkpoint.target_time_et.strftime("%H:%M"),
                "mode": checkpoint.mode.value,
                "grace_minutes_before": checkpoint.grace_minutes_before,
                "grace_minutes_after": checkpoint.grace_minutes_after,
                "required": checkpoint.required,
                "session": checkpoint.session,
                "trading_day_offset": checkpoint.trading_day_offset,
            }
            for checkpoint in schedule.checkpoints
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _symbols_hash(symbols: tuple[str, ...]) -> str:
    return sha256(json.dumps(list(symbols)).encode("utf-8")).hexdigest()


def _serialize_schedule_tape(tape: ScheduleTape) -> dict[str, object]:
    return {
        "anchor_trade_date": tape.anchor_trade_date.isoformat(),
        "materialized_on_trade_date": _materialized_on_trade_date(tape).isoformat(),
        "checkpoint_trade_dates": {
            checkpoint: trade_date.isoformat()
            for checkpoint, trade_date in sorted(tape.checkpoint_trade_dates.items())
        },
        "observations": {
            checkpoint: {
                symbol: _serialize_observation(observation)
                for symbol, observation in sorted(by_symbol.items())
            }
            for checkpoint, by_symbol in sorted(tape.observations.items())
        },
    }


def _deserialize_schedule_tape(payload: dict[str, object]) -> ScheduleTape:
    checkpoint_trade_dates = {
        checkpoint: date.fromisoformat(trade_date_str)
        for checkpoint, trade_date_str in dict(payload["checkpoint_trade_dates"]).items()
    }
    observations = {
        checkpoint: {
            symbol: _deserialize_observation(observation)
            for symbol, observation in dict(by_symbol).items()
        }
        for checkpoint, by_symbol in dict(payload["observations"]).items()
    }
    return ScheduleTape(
        anchor_trade_date=date.fromisoformat(str(payload["anchor_trade_date"])),
        checkpoint_trade_dates=checkpoint_trade_dates,
        observations=observations,
        audit_records=[],
    )


def _serialize_observation(observation: CheckpointObservation | None) -> dict[str, object] | None:
    if observation is None:
        return None
    return {
        "symbol": observation.symbol,
        "checkpoint": observation.checkpoint,
        "anchor_trade_date": observation.anchor_trade_date.isoformat(),
        "trade_date": observation.trade_date.isoformat(),
        "price": observation.price,
        "ts_utc": observation.ts_utc.isoformat(sep=" "),
        "ts_et": observation.ts_et.isoformat(),
        "resolution": observation.resolution,
    }


def _deserialize_observation(payload: dict[str, object] | None) -> CheckpointObservation | None:
    if payload is None:
        return None
    return CheckpointObservation(
        symbol=str(payload["symbol"]),
        checkpoint=str(payload["checkpoint"]),
        anchor_trade_date=date.fromisoformat(str(payload["anchor_trade_date"])),
        trade_date=date.fromisoformat(str(payload["trade_date"])),
        price=float(payload["price"]),
        ts_utc=datetime.fromisoformat(str(payload["ts_utc"])),
        ts_et=datetime.fromisoformat(str(payload["ts_et"])),
        resolution=str(payload["resolution"]),
    )