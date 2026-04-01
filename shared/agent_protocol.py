# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  DO NOT CHANGE WITHOUT USER APPROVAL                                       ║
# ║  This is critical orchestrator infrastructure. Any modification risks       ║
# ║  breaking agent-to-orchestrator communication mid-experiment.               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
"""
APC — Agent Process Communication (Writer Side)

Filesystem-based IPC for sub-agents and scripts to report progress to the
orchestrator. Sub-agents in Claude Code cannot stream output back to the
parent conversation. APC bridges this gap by writing structured JSONL
messages to /tmp/claudodidact/{channel_id}/messages.jsonl.

Architecture
------------
This file contains the WRITER side only — classes that agents and scripts
use to emit messages. The READER side (orchestrator-facing) lives in
shared/apc.py, which provides status, read, wait, and clean commands.

    Writers (this file)              Readers (shared/apc.py)
    ──────────────────               ──────────────────────
    AgentChannel  ──writes──>  messages.jsonl  <──reads──  apc.read_new()
    ScriptProgress ──writes──>  messages.jsonl  <──reads──  apc.wait_channel()

Three integration levels
------------------------
1. Agent-level (AgentChannel):
   One per sub-agent (experiment, reviewer, adversary). Reports step-level
   progress: STEP_0 complete, STEP_1 complete, etc. Created at agent startup,
   writes HEARTBEAT on init, COMPLETE or ERROR on finish.

2. Script-level (ScriptProgress):
   Used INSIDE long-running scripts (build_dataset.py, run_strategy.py).
   Attaches to an existing AgentChannel and writes TICK messages for
   inner-loop progress (day 50/1029). Throttled to one write per 30 seconds
   to avoid flooding the channel.

3. Orchestrator-level (shared/apc.py — NOT this file):
   Reads channels, computes staleness, provides blocking wait and
   incremental cursor-tracked reads. See shared/apc.py.

Message types
-------------
- HEARTBEAT: Agent/script is alive (emitted on init and periodically)
- PROGRESS:  A pipeline step or sub-step completed
- TICK:      Inner-loop progress (day N/M, throttled)
- WARNING:   Non-fatal issue (slow query, low disk)
- ERROR:     Fatal issue, agent stopping
- COMPLETE:  Agent finished successfully with summary metrics

Channel layout on disk
----------------------
/tmp/claudodidact/
  {channel_id}/
    messages.jsonl   — Append-only JSONL, one message per line
    writer.lock      — PID of the active writer (double-writer detection)
  .cursors/
    {channel_id}     — Last-read line number (managed by shared/apc.py)

Safety guarantees
-----------------
- Single-writer enforced via writer.lock (PID-based, atomic creation)
- Append-only JSONL is POSIX-safe for single-writer without locking
- Partial lines (writer mid-write when reader opens) are handled by
  shared/apc.py's read_jsonl_safe() which skips malformed JSON
- Orphaned locks (dead writer PID) are automatically taken over

Usage examples
--------------
Agent (in experiment-agent prompt):
    from shared.agent_protocol import AgentChannel
    channel = AgentChannel("cycle5_experiment_06", "06_base_overnight")
    channel.progress("STEP_0", "Pre-flight complete", {"items": 7})
    channel.progress("STEP_1", "Dataset built", {"rows": 1029})
    channel.complete({"sharpe_test": 0.84, "score": "28/28"})

Script (inside build_dataset.py):
    from shared.agent_protocol import ScriptProgress
    progress = ScriptProgress.attach("cycle5_experiment_06")
    progress.start("build_dataset", total=len(trading_days))
    for i, day in enumerate(trading_days):
        # ... query and process ...
        progress.tick(i + 1)  # only writes if 30s elapsed since last tick
    progress.done({"rows": len(df), "size_mb": df.memory_usage().sum()/1e6})
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

# All channels live under this directory. Using /tmp/ ensures cleanup on reboot
# and keeps experiment data separate from the git repo.
PROTOCOL_DIR = Path("/tmp/claudodidact")


# ═══════════════════════════════════════════════════════════════════════════════
# Message Schema
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResourceSnapshot:
    """Point-in-time system resource reading, attached to every APC message.

    Captured cheaply on each write (only disk_free_gb is populated by default).
    Allows the orchestrator to detect resource degradation over time without
    a separate monitoring poll.
    """
    disk_free_gb: float = 0.0
    memory_used_pct: float = 0.0
    cpu_pct: float = 0.0
    db_connections: int = 0
    db_active_queries: int = 0


@dataclass
class AgentMessage:
    """Single message in an APC channel. Serialized as one JSON line in messages.jsonl.

    Fields:
        timestamp:       ISO 8601 wall-clock time (used for staleness detection)
        agent_id:        Channel identifier (e.g., "cycle5_experiment_06")
        experiment:      Human-readable experiment name (e.g., "06_base_overnight")
        msg_type:        HEARTBEAT | PROGRESS | TICK | WARNING | ERROR | COMPLETE
        step:            Pipeline step (INIT, STEP_0..STEP_4, CLEANUP, DONE,
                         or SCRIPT_{name} for script-level ticks)
        message:         Human-readable description of what happened
        metrics:         Arbitrary dict of numeric metrics (rows, elapsed_min, etc.)
        resources:       System resource snapshot at write time
        elapsed_seconds: Wall-clock seconds since the AgentChannel was created
    """
    timestamp: str
    agent_id: str
    experiment: str
    msg_type: str          # HEARTBEAT | PROGRESS | TICK | WARNING | ERROR | COMPLETE
    step: str              # INIT | STEP_0 | STEP_1 | STEP_2 | STEP_3 | STEP_4 | CLEANUP | DONE
    message: str
    metrics: dict = field(default_factory=dict)
    resources: ResourceSnapshot | dict = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def to_json(self) -> str:
        """Serialize to a single JSON line for JSONL output."""
        d = asdict(self)
        return json.dumps(d, default=str)

    @classmethod
    def from_json(cls, line: str) -> AgentMessage:
        """Deserialize from a single JSON line. Reconstructs ResourceSnapshot."""
        d = json.loads(line)
        if isinstance(d.get("resources"), dict):
            d["resources"] = ResourceSnapshot(**d["resources"]) if d["resources"] else ResourceSnapshot()
        return cls(**d)


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Writer — one per sub-agent (experiment, reviewer, adversary)
# ═══════════════════════════════════════════════════════════════════════════════


class AgentChannel:
    """Write-only channel for a sub-agent to report progress to the orchestrator.

    Lifecycle:
        1. __init__: Creates channel dir, writes writer.lock, emits HEARTBEAT INIT
        2. progress/heartbeat/warning: Called between pipeline steps
        3. complete() or error(): Terminal message, signals the agent is done

    The orchestrator reads these messages via `python -m shared.apc read <channel>`
    or blocks on `python -m shared.apc wait <channel>`.

    Writer lock:
        On init, creates writer.lock with the current PID using O_CREAT|O_EXCL
        (atomic — fails if file exists). If the lock already exists:
        - If the holder PID is alive: warns to stderr (double-writer detected)
        - If the holder PID is dead: takes over the lock (orphaned process)
        Every _write() call verifies lock ownership as a runtime safeguard.
    """

    def __init__(self, agent_id: str, experiment: str):
        self.agent_id = agent_id
        self.experiment = experiment
        self.channel_dir = PROTOCOL_DIR / agent_id
        self.channel_dir.mkdir(parents=True, exist_ok=True)
        self.channel_file = self.channel_dir / "messages.jsonl"
        self.start_time = time.monotonic()
        self._pid = os.getpid()

        # Writer lock — atomic creation detects double-writers.
        # O_CREAT|O_EXCL fails if file exists, preventing race conditions.
        self._lock_file = self.channel_dir / "writer.lock"
        try:
            fd = os.open(str(self._lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(self._pid).encode())
            os.close(fd)
        except FileExistsError:
            # Lock already exists — check if the holder is still alive
            try:
                existing_pid = int(self._lock_file.read_text().strip())
                os.kill(existing_pid, 0)  # signal 0 = check alive, don't kill
                print(f"WARNING: Channel {agent_id} already has active writer PID {existing_pid}",
                      file=__import__('sys').stderr)
            except (OSError, ValueError):
                # Dead process or corrupt lock — safe to take over
                self._lock_file.write_text(str(self._pid))

        self._write("HEARTBEAT", "INIT", "Agent started")

    def _elapsed(self) -> float:
        """Seconds since this channel was created (monotonic, not wall-clock)."""
        return round(time.monotonic() - self.start_time, 1)

    def _snapshot(self) -> ResourceSnapshot:
        """Quick resource snapshot. Only populates disk_free_gb to stay cheap.

        Full system health (memory, CPU, DB) is checked by the orchestrator
        via `python -m shared.apc status` — agents don't need to duplicate that.
        """
        try:
            import shutil
            _, _, free = shutil.disk_usage("/")
            disk_gb = free / (1024 ** 3)
        except Exception:
            disk_gb = 0.0
        return ResourceSnapshot(disk_free_gb=round(disk_gb, 1))

    def _write(self, msg_type: str, step: str, message: str, metrics: dict | None = None):
        """Append one JSONL message to the channel file.

        Before writing, verifies lock ownership to catch double-writer bugs
        at runtime (not just at init). Logs to stderr on violation but does
        not raise — the message still gets written so the orchestrator sees it.
        """
        # Verify lock ownership (detect double-writers)
        if hasattr(self, '_lock_file') and self._lock_file.exists():
            try:
                lock_pid = int(self._lock_file.read_text().strip())
                if lock_pid != self._pid:
                    print(f"ERROR: Double-writer on {self.agent_id}: "
                          f"lock PID {lock_pid} != this PID {self._pid}",
                          file=__import__('sys').stderr)
            except (ValueError, OSError):
                pass

        msg = AgentMessage(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            agent_id=self.agent_id,
            experiment=self.experiment,
            msg_type=msg_type,
            step=step,
            message=message,
            metrics=metrics or {},
            resources=self._snapshot(),
            elapsed_seconds=self._elapsed(),
        )
        with open(self.channel_file, "a") as f:
            f.write(msg.to_json() + "\n")

    def heartbeat(self, step: str, message: str):
        """Agent is alive but no step completed. Use between long operations."""
        self._write("HEARTBEAT", step, message)

    def progress(self, step: str, message: str, metrics: dict | None = None):
        """A pipeline step or sub-step completed. Include metrics if available."""
        self._write("PROGRESS", step, message, metrics)

    def warning(self, step: str, message: str, metrics: dict | None = None):
        """Non-fatal issue detected (slow query, low disk, unexpected data)."""
        self._write("WARNING", step, message, metrics)

    def error(self, step: str, message: str, metrics: dict | None = None):
        """Fatal issue — agent is stopping. Terminal message."""
        self._write("ERROR", step, message, metrics)

    def complete(self, metrics: dict | None = None):
        """Agent finished successfully. Include final summary metrics.

        This is a terminal message — the orchestrator's `apc wait` command
        unblocks when it sees COMPLETE or ERROR.
        """
        self._write("COMPLETE", "DONE", "Agent finished", metrics)


# ═══════════════════════════════════════════════════════════════════════════════
# Script-Level Progress — used INSIDE build_dataset.py, run_strategy.py, etc.
#
# Attaches to an existing AgentChannel and writes throttled TICK messages
# for inner-loop progress. Avoids flooding: writes at most once per
# min_interval seconds (default 30s).
# ═══════════════════════════════════════════════════════════════════════════════


class ScriptProgress:
    """Lightweight progress reporter for use inside experiment scripts.

    Attaches to an existing AgentChannel (created by the agent that launched
    the script) and writes TICK messages for inner-loop progress. Throttled
    to one write per min_interval seconds to avoid flooding the JSONL file.

    Also auto-appends one-liners to PROGRESS.md in the experiment directory
    if that file's parent directory exists.

    Usage:
        from shared.agent_protocol import ScriptProgress

        progress = ScriptProgress.attach("cycle5_experiment_06")
        progress.start("build_dataset", total=1029)

        for i, day in enumerate(trading_days):
            # ... do work ...
            progress.tick(i + 1)  # only writes if 30s elapsed since last tick

        progress.done({"rows": 1029, "size_mb": 0.08})
    """

    def __init__(self, channel: AgentChannel, min_interval: float = 30.0):
        self.channel = channel
        self.min_interval = min_interval  # seconds between TICK writes
        self._script_name = ""
        self._total = 0
        self._last_tick_time = 0.0
        self._start_time = 0.0

    @classmethod
    def attach(cls, agent_id: str, experiment: str = "", min_interval: float = 30.0) -> ScriptProgress:
        """Attach to an existing agent channel (or create one if none exists).

        If the channel's messages.jsonl already exists, reuses it without
        writing a new INIT heartbeat (avoids duplicate inits when the agent
        already created the channel). If no channel exists, creates a new one.
        """
        channel_file = PROTOCOL_DIR / agent_id / "messages.jsonl"
        if channel_file.exists():
            # Reuse existing channel — bypass __init__ to avoid duplicate INIT
            ch = AgentChannel.__new__(AgentChannel)
            ch.agent_id = agent_id
            ch.experiment = experiment
            ch.channel_dir = PROTOCOL_DIR / agent_id
            ch.channel_file = channel_file
            ch.start_time = time.monotonic()
        else:
            # No channel yet — create one (writes INIT heartbeat + lock)
            ch = AgentChannel(agent_id, experiment)
        return cls(ch, min_interval)

    @classmethod
    def try_attach(cls, min_interval: float = 30.0) -> ScriptProgress | None:
        """Try to attach to the most recently active channel.

        Returns None if no channels exist. Useful for scripts that don't
        know their channel ID — picks the most recently modified one.
        """
        channels = list_active_channels()
        if not channels:
            return None
        # Pick the most recently modified channel
        latest = max(channels, key=lambda c: (PROTOCOL_DIR / c / "messages.jsonl").stat().st_mtime)
        return cls.attach(latest, min_interval=min_interval)

    def start(self, script_name: str, total: int = 0):
        """Signal that a script is starting a loop. Call before the loop begins.

        Args:
            script_name: Identifier (e.g., "build_dataset", "run_strategy")
            total: Total iterations expected (0 if unknown). Enables ETA calculation.
        """
        self._script_name = script_name
        self._total = total
        self._start_time = time.monotonic()
        self._last_tick_time = self._start_time
        self.channel._write("PROGRESS", f"SCRIPT_{script_name}",
                           f"Starting {script_name}" + (f" ({total} items)" if total else ""))

    def tick(self, current: int, message: str = ""):
        """Report inner-loop progress. Throttled: only writes if min_interval elapsed.

        Computes percentage and ETA from current/total. If min_interval hasn't
        elapsed since the last tick, this is a no-op (returns immediately).

        Args:
            current: Current iteration number (1-indexed)
            message: Optional custom message (auto-generated from current/total if empty)
        """
        now = time.monotonic()
        if now - self._last_tick_time < self.min_interval:
            return  # throttle — don't flood the channel

        self._last_tick_time = now
        elapsed = now - self._start_time
        pct = (current / self._total * 100) if self._total > 0 else 0
        eta_seconds = (elapsed / current * (self._total - current)) if current > 0 and self._total > 0 else 0

        if not message:
            message = f"{current}/{self._total}" if self._total else str(current)

        if self._total > 0:
            message += f" ({pct:.0f}%, ETA {eta_seconds/60:.1f}m)"

        self.channel._write("TICK", f"SCRIPT_{self._script_name}", message,
                           {"current": current, "total": self._total, "pct": round(pct, 1),
                            "elapsed_seconds": round(elapsed, 1), "eta_seconds": round(eta_seconds, 1)})

        # Also write to PROGRESS.md for human-readable audit trail
        self._write_progress_md(message)

    def done(self, metrics: dict | None = None):
        """Signal that the script's loop completed. Include summary metrics."""
        elapsed = time.monotonic() - self._start_time
        self.channel._write("PROGRESS", f"SCRIPT_{self._script_name}",
                           f"Completed in {elapsed/60:.1f} min", metrics)

    def _write_progress_md(self, message: str):
        """Append a one-liner to PROGRESS.md in the experiment directory.

        Tries cwd first (script running from experiment dir), then parent
        (script running from a subdirectory). Silently skips if no suitable
        directory exists — PROGRESS.md is a nice-to-have audit trail, not
        a critical path.
        """
        for candidate in [
            Path.cwd() / "PROGRESS.md",
            Path.cwd().parent / "PROGRESS.md",
        ]:
            if candidate.parent.exists():
                try:
                    with open(candidate, "a") as f:
                        f.write(f"- [{datetime.now().strftime('%H:%M:%S')}] {self._script_name}: {message}\n")
                    return
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestrator Reader — legacy functions kept for backward compatibility.
#
# PREFERRED: Use `python -m shared.apc` commands instead. These functions
# are still imported by shared/apc.py.
# ═══════════════════════════════════════════════════════════════════════════════


def list_active_channels() -> list[str]:
    """List all agent channel IDs that have a messages.jsonl file."""
    if not PROTOCOL_DIR.exists():
        return []
    return [d.name for d in PROTOCOL_DIR.iterdir()
            if d.is_dir() and d.name != ".cursors" and (d / "messages.jsonl").exists()]


def read_channel(agent_id: str, after_line: int = 0) -> list[AgentMessage]:
    """Read messages from a channel, optionally skipping already-seen lines.

    NOTE: No error handling on malformed JSON. For safe reading, use
    shared/apc.read_jsonl_safe() instead.
    """
    channel_file = PROTOCOL_DIR / agent_id / "messages.jsonl"
    if not channel_file.exists():
        return []
    messages = []
    with open(channel_file) as f:
        for i, line in enumerate(f):
            if i < after_line:
                continue
            line = line.strip()
            if line:
                messages.append(AgentMessage.from_json(line))
    return messages


def read_latest(agent_id: str) -> AgentMessage | None:
    """Read the most recent message from a channel.

    Scans the entire file to find the last line. For large channels,
    consider using shared/apc.read_new() with cursor tracking instead.
    """
    channel_file = PROTOCOL_DIR / agent_id / "messages.jsonl"
    if not channel_file.exists():
        return None
    last_line = None
    with open(channel_file) as f:
        for line in f:
            if line.strip():
                last_line = line.strip()
    return AgentMessage.from_json(last_line) if last_line else None


def cleanup_channel(agent_id: str):
    """Remove a channel directory (messages.jsonl + writer.lock).

    PREFERRED: Use `python -m shared.apc clean <channel>` which also
    cleans up cursor files.
    """
    import shutil
    channel_dir = PROTOCOL_DIR / agent_id
    if channel_dir.exists():
        shutil.rmtree(channel_dir)


def print_dashboard():
    """Print a compact dashboard of all active agents.

    DEPRECATED: Use `python -m shared.apc status` instead, which also
    includes DB health and system health.
    """
    channels = list_active_channels()
    if not channels:
        print("No active agents.")
        return

    now = datetime.now()
    print()
    print(f"{'Agent':<30} {'Step':<20} {'Status':<10} {'Stale':>8} {'Msgs':>5} {'Last Message':<40}")
    print("-" * 120)

    for agent_id in sorted(channels):
        msg = read_latest(agent_id)
        if msg is None:
            continue

        # Compute staleness from wall-clock timestamp (not elapsed_seconds)
        try:
            msg_time = datetime.fromisoformat(msg.timestamp)
            stale_seconds = (now - msg_time).total_seconds()
            if stale_seconds < 60:
                stale_str = f"{stale_seconds:.0f}s"
            elif stale_seconds < 3600:
                stale_str = f"{stale_seconds/60:.0f}m"
            else:
                stale_str = f"{stale_seconds/3600:.1f}h"
            if stale_seconds > 600:
                stale_str = f"⚠{stale_str}"
        except Exception:
            stale_str = "?"

        # Count total messages in channel
        channel_file = PROTOCOL_DIR / agent_id / "messages.jsonl"
        try:
            msg_count = sum(1 for _ in open(channel_file))
        except Exception:
            msg_count = 0

        status = msg.msg_type
        truncated_msg = msg.message[:40]
        print(f"{agent_id:<30} {msg.step:<20} {status:<10} {stale_str:>8} {msg_count:>5} {truncated_msg:<40}")

    print()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — Legacy entry point. Prefer `python -m shared.apc` for orchestrator use.
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Agent communication protocol monitor")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("dashboard", help="Show all active agents")
    sub.add_parser("list", help="List active agent channels")

    tail_cmd = sub.add_parser("tail", help="Follow an agent's messages")
    tail_cmd.add_argument("agent_id")

    watch_cmd = sub.add_parser("watch", help="Continuously poll all agents")
    watch_cmd.add_argument("--interval", type=int, default=10)

    clean_cmd = sub.add_parser("clean", help="Remove all agent channels")
    health_cmd = sub.add_parser("health", help="Full health check: agents + DB + system")
    health_cmd.add_argument("--compact", action="store_true", help="One-line system summary")

    args = parser.parse_args()

    if args.command == "health":
        import subprocess, sys as _sys
        print_dashboard()
        _sys.stdout.flush()
        if getattr(args, "compact", False):
            try:
                result = subprocess.run([_sys.executable, "-m", "shared.db_monitor", "status"],
                                       capture_output=True, text=True, timeout=5)
                db_line = result.stdout.strip().replace("\n", " | ")
            except Exception:
                db_line = "DB: unknown"
            sys_msg = read_latest("system_monitor")
            sys_line = sys_msg.message[:60] if sys_msg else "system_monitor not running"
            print(f"{sys_line} | {db_line}")
        else:
            print("--- DB ---")
            _sys.stdout.flush()
            subprocess.run([_sys.executable, "-m", "shared.db_monitor", "status"])
            print("\n--- System ---")
            _sys.stdout.flush()
            subprocess.run([_sys.executable, "-m", "shared.system_monitor"])

    elif args.command == "dashboard":
        print_dashboard()

    elif args.command == "list":
        for ch in list_active_channels():
            msg = read_latest(ch)
            status = msg.msg_type if msg else "?"
            print(f"  {ch}: {status}")

    elif args.command == "tail":
        seen = 0
        while True:
            messages = read_channel(args.agent_id, after_line=seen)
            for msg in messages:
                print(f"[{msg.timestamp}] {msg.step:>7} {msg.msg_type:<10} {msg.message}")
                if msg.metrics:
                    print(f"         metrics: {json.dumps(msg.metrics)}")
                seen += 1
            if any(m.msg_type in ("COMPLETE", "ERROR") for m in messages):
                break
            time.sleep(3)

    elif args.command == "watch":
        while True:
            os.system("clear")
            print_dashboard()
            time.sleep(args.interval)

    elif args.command == "clean":
        for ch in list_active_channels():
            cleanup_channel(ch)
            print(f"  Cleaned: {ch}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
