# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  WARNING: Do not modify this file without user permission.                 ║
# ║  This is critical orchestrator infrastructure.                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
"""
apc.py — Unified APC (Agent Process Communication) orchestrator interface.

Single entry point for all orchestrator-facing APC operations:
    python -m shared.apc status                     # compact dashboard (APC + DB + system)
    python -m shared.apc read <channel> --new       # incremental read (cursor-tracked)
    python -m shared.apc read <channel> --since N   # read after line N
    python -m shared.apc wait <channel> --timeout 600   # block until COMPLETE/ERROR/timeout
    python -m shared.apc wait --any --timeout 600       # block until any channel finishes
    python -m shared.apc monitor <channel> --interval 180  # poll status every 3 min until done
    python -m shared.apc clean [channel]            # remove channels + cursors

Reads from the JSONL channels written by AgentChannel and ScriptProgress
(defined in shared/agent_protocol.py — writers stay there).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from shared.agent_protocol import (
    PROTOCOL_DIR,
    AgentMessage,
    cleanup_channel as _cleanup_channel,
    list_active_channels,
    read_latest,
)

CURSOR_DIR = PROTOCOL_DIR / ".cursors"

# Terminal message types — channels in these states are "done"
_TERMINAL_TYPES = frozenset({"COMPLETE", "ERROR"})


# ═══════════════════════════════════════════════════════════════════════════════
# Safe JSONL Reader
# ═══════════════════════════════════════════════════════════════════════════════


def read_jsonl_safe(
    path: Path, after_line: int = 0
) -> list[tuple[int, AgentMessage]]:
    """Read JSONL file, skipping malformed lines (partial writes, corruption).

    Returns list of (line_number, message) tuples. Line numbers are 0-indexed.
    """
    if not path.exists():
        return []
    results: list[tuple[int, AgentMessage]] = []
    with open(path) as f:
        for i, line in enumerate(f):
            if i < after_line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                results.append((i, AgentMessage.from_json(line)))
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                print(f"  [warn] Skipping malformed line {i} in {path.name}: {e}", file=sys.stderr)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Cursor-Based Incremental Reading
# ═══════════════════════════════════════════════════════════════════════════════


def _read_cursor(channel: str) -> int:
    """Read the last-seen line number for a channel."""
    cursor_file = CURSOR_DIR / channel
    if cursor_file.exists():
        try:
            return int(cursor_file.read_text().strip())
        except (ValueError, OSError):
            return 0
    return 0


def _write_cursor(channel: str, line_number: int):
    """Write the last-seen line number for a channel."""
    CURSOR_DIR.mkdir(parents=True, exist_ok=True)
    (CURSOR_DIR / channel).write_text(str(line_number))


def read_new(channel: str) -> list[AgentMessage]:
    """Read only messages since last read. Updates cursor on success."""
    after = _read_cursor(channel)
    path = PROTOCOL_DIR / channel / "messages.jsonl"
    results = read_jsonl_safe(path, after_line=after)
    if results:
        last_line = results[-1][0]
        _write_cursor(channel, last_line + 1)
    return [msg for _, msg in results]


def read_since(channel: str, since: int) -> list[AgentMessage]:
    """Read messages after a specific line number (0-indexed)."""
    path = PROTOCOL_DIR / channel / "messages.jsonl"
    return [msg for _, msg in read_jsonl_safe(path, after_line=since)]


# ═══════════════════════════════════════════════════════════════════════════════
# Blocking Wait
# ═══════════════════════════════════════════════════════════════════════════════


def wait_channel(
    channel: str, timeout: int = 600, poll_interval: float = 3.0,
    on_step: bool = False,
) -> AgentMessage | None:
    """Block until a channel reaches COMPLETE/ERROR (or next step if on_step=True).

    Args:
        channel: APC channel ID
        timeout: max wait seconds
        poll_interval: internal poll frequency (cheap, no API calls)
        on_step: if True, return on any STEP_*_COMPLETE or STEP_*_START transition
                 (not just terminal). This lets the orchestrator do work at step
                 boundaries without polling between them.

    Returns the triggering message, or None on timeout.
    Designed for `run_in_background` — orchestrator gets notified without polling.
    """
    deadline = time.monotonic() + timeout
    last_step: str | None = None
    while time.monotonic() < deadline:
        msg = read_latest(channel)
        if msg and msg.msg_type in _TERMINAL_TYPES:
            return msg
        if on_step and msg:
            current_step = msg.step
            if last_step is None:
                last_step = current_step
            elif current_step != last_step:
                last_step = current_step
                return msg  # step changed — return so orchestrator can act
        time.sleep(poll_interval)
    return None


def wait_any(
    timeout: int = 600, poll_interval: float = 3.0
) -> tuple[str, AgentMessage] | None:
    """Block until ANY channel reaches COMPLETE/ERROR, or timeout.

    Returns (channel_name, terminal_message) or None on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for channel in list_active_channels():
            msg = read_latest(channel)
            if msg and msg.msg_type in _TERMINAL_TYPES:
                return (channel, msg)
        time.sleep(poll_interval)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Stale Channel Detection
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_staleness(msg: AgentMessage) -> float | None:
    """Compute seconds since a message was written. None if unparseable."""
    try:
        msg_time = datetime.fromisoformat(msg.timestamp)
        return (datetime.now() - msg_time).total_seconds()
    except Exception:
        return None


def detect_stale_channels(threshold_seconds: float = 600) -> list[tuple[str, float]]:
    """Find channels with no recent activity that haven't completed.

    Returns list of (channel, stale_seconds) for channels exceeding threshold.
    """
    stale = []
    for channel in list_active_channels():
        msg = read_latest(channel)
        if msg is None:
            continue
        if msg.msg_type in _TERMINAL_TYPES:
            continue  # finished channels aren't "stale"
        seconds = _compute_staleness(msg)
        if seconds is not None and seconds > threshold_seconds:
            stale.append((channel, seconds))
    return stale


# ═══════════════════════════════════════════════════════════════════════════════
# Lock File Checks (reader-side — detects double-writer problems)
# ═══════════════════════════════════════════════════════════════════════════════


def check_writer_lock(channel: str) -> dict:
    """Check the writer lock file for a channel. Returns lock status."""
    lock_file = PROTOCOL_DIR / channel / "writer.lock"
    if not lock_file.exists():
        return {"locked": False, "status": "no_lock"}
    try:
        pid = int(lock_file.read_text().strip())
        # Check if the PID is still alive
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
        return {"locked": True, "pid": pid, "alive": alive,
                "status": "active" if alive else "orphaned"}
    except (ValueError, OSError):
        return {"locked": True, "status": "corrupt"}


# ═══════════════════════════════════════════════════════════════════════════════
# Status Dashboard
# ═══════════════════════════════════════════════════════════════════════════════


def _format_stale(seconds: float | None) -> str:
    """Format staleness as human-readable string."""
    if seconds is None:
        return "?"
    if seconds < 60:
        s = f"{seconds:.0f}s"
    elif seconds < 3600:
        s = f"{seconds / 60:.0f}m"
    else:
        s = f"{seconds / 3600:.1f}h"
    if seconds > 600:
        s = f"⚠{s}"
    return s


def _get_db_health() -> dict:
    """Get DB connection count and long-running queries."""
    try:
        from shared.db_monitor import safe_connection, get_connection_count, get_active_queries
        with safe_connection(statement_timeout="10s") as conn:
            total = get_connection_count(conn)
            active = get_active_queries(conn)
            long_queries = [q for q in active if q.duration_seconds > 10]
            status = "CRITICAL" if total > 4 else ("WARNING" if long_queries else "OK")
            return {
                "connections": total,
                "active": len(active),
                "long_queries": long_queries,
                "status": status,
            }
    except Exception as e:
        return {"connections": "?", "active": "?", "long_queries": [], "status": f"ERROR: {e}"}


def _get_system_health() -> dict:
    """Get disk/memory/CPU health."""
    try:
        from shared.system_monitor import get_disk_usage, get_memory_usage, get_cpu_usage
        disk = get_disk_usage()
        mem = get_memory_usage()
        cpu = get_cpu_usage()
        statuses = [disk["status"], mem["status"], cpu["status"]]
        overall = "CRITICAL" if "CRITICAL" in statuses else (
            "WARNING" if "WARNING" in statuses else "OK"
        )
        return {"disk": disk, "memory": mem, "cpu": cpu, "status": overall}
    except Exception as e:
        return {"disk": {}, "memory": {}, "cpu": {}, "status": f"ERROR: {e}"}


def print_status() -> str:
    """Print unified status dashboard. Returns overall status string."""
    channels = list_active_channels()
    db = _get_db_health()
    system = _get_system_health()

    # Overall status
    statuses = [db.get("status", "OK"), system.get("status", "OK")]
    overall = "CRITICAL" if any("CRITICAL" in str(s) for s in statuses) else (
        "WARNING" if any("WARNING" in str(s) for s in statuses) else "OK"
    )

    print(f"{'=' * 70}")
    print(f"  APC STATUS  [{overall}]")
    print(f"{'=' * 70}")

    # System health — one line each
    disk = system.get("disk", {})
    mem = system.get("memory", {})
    cpu = system.get("cpu", {})
    if disk:
        print(f"  Disk    {disk.get('free_gb', '?'):.1f} GB free / {disk.get('total_gb', '?'):.0f} GB  [{disk.get('status', '?')}]")
    if mem:
        print(f"  Memory  {mem.get('used_gb', '?'):.1f} / {mem.get('total_gb', '?'):.1f} GB ({mem.get('pct', '?'):.0f}%)  [{mem.get('status', '?')}]")
    if cpu:
        print(f"  CPU     {cpu.get('cores', '?')} cores, {cpu.get('pct', '?'):.0f}% load  [{cpu.get('status', '?')}]")

    # DB health
    print(f"  DB      {db.get('connections', '?')} connections, {db.get('active', '?')} active  [{db.get('status', '?')}]")
    for q in db.get("long_queries", []):
        print(f"    ⚠ PID {q.pid} running {q.duration_seconds:.0f}s: {q.query[:60]}")

    # APC channels
    if channels:
        print(f"{'-' * 70}")
        print(f"  {'Agent':<30} {'Step':<15} {'Type':<10} {'Stale':>7} {'Msgs':>5}  {'Message'}")
        print(f"{'-' * 70}")
        for ch in sorted(channels):
            msg = read_latest(ch)
            if msg is None:
                print(f"  {ch:<30} {'?':<15} {'?':<10} {'?':>7} {'0':>5}  (empty)")
                continue

            stale = _compute_staleness(msg)
            stale_str = _format_stale(stale)

            # Count messages
            channel_file = PROTOCOL_DIR / ch / "messages.jsonl"
            try:
                msg_count = sum(1 for _ in open(channel_file))
            except Exception:
                msg_count = 0

            # Check lock status
            lock = check_writer_lock(ch)
            lock_flag = ""
            if lock.get("status") == "orphaned":
                lock_flag = " [ORPHAN]"

            truncated = msg.message[:35]
            print(f"  {ch:<30} {msg.step:<15} {msg.msg_type:<10} {stale_str:>7} {msg_count:>5}  {truncated}{lock_flag}")
    else:
        print("  No active agents.")

    # Stale warnings
    stale_channels = detect_stale_channels()
    for ch, secs in stale_channels:
        print(f"  ⚠ STALE: {ch} — no activity for {secs / 60:.0f} min (agent may be dead)")

    print(f"{'=' * 70}")
    return overall


# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════════════════════


def clean_channel(channel: str):
    """Remove a channel directory, cursor, and lock file."""
    _cleanup_channel(channel)
    cursor_file = CURSOR_DIR / channel
    cursor_file.unlink(missing_ok=True)
    print(f"  Cleaned: {channel}")


def clean_all():
    """Remove all channels and orphan cursors."""
    for ch in list_active_channels():
        clean_channel(ch)
    # Clean orphan cursors (channels already removed)
    if CURSOR_DIR.exists():
        for cursor_file in CURSOR_DIR.iterdir():
            if cursor_file.is_file():
                channel_dir = PROTOCOL_DIR / cursor_file.name
                if not channel_dir.exists():
                    cursor_file.unlink()
                    print(f"  Cleaned orphan cursor: {cursor_file.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


def _print_messages(messages: list[AgentMessage]):
    """Format and print a list of messages."""
    for msg in messages:
        print(f"  [{msg.timestamp}] {msg.step:<15} {msg.msg_type:<10} {msg.message}")
        if msg.metrics:
            print(f"    metrics: {json.dumps(msg.metrics)}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="APC — Agent Process Communication (orchestrator interface)",
        prog="python -m shared.apc",
    )
    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Compact dashboard: APC + DB + system health")

    # read
    read_cmd = sub.add_parser("read", help="Read messages from a channel")
    read_cmd.add_argument("channel", help="Channel ID")
    read_group = read_cmd.add_mutually_exclusive_group()
    read_group.add_argument("--new", action="store_true",
                            help="Only messages since last read (cursor-tracked)")
    read_group.add_argument("--since", type=int, default=None,
                            help="Messages after line number N")

    # wait
    wait_cmd = sub.add_parser("wait", help="Block until channel finishes or timeout")
    wait_cmd.add_argument("channel", nargs="?", default=None, help="Channel ID")
    wait_cmd.add_argument("--any", action="store_true", dest="wait_any",
                          help="Wait for ANY channel to finish")
    wait_cmd.add_argument("--on-step", action="store_true", dest="on_step",
                          help="Return on each step transition (not just COMPLETE/ERROR)")
    wait_cmd.add_argument("--timeout", type=int, default=600,
                          help="Timeout in seconds (default: 600)")

    # monitor
    mon_cmd = sub.add_parser("monitor", help="Poll status on interval until channel completes")
    mon_cmd.add_argument("channel", help="Channel ID to watch")
    mon_cmd.add_argument("--interval", type=int, default=180,
                         help="Seconds between polls (default: 180)")
    mon_cmd.add_argument("--timeout", type=int, default=3600,
                         help="Max total wait seconds (default: 3600)")

    # clean
    clean_cmd = sub.add_parser("clean", help="Remove channels and cursors")
    clean_cmd.add_argument("channel", nargs="?", default=None,
                           help="Specific channel (omit for all)")

    args = parser.parse_args()

    if args.command == "status":
        status = print_status()
        if status == "CRITICAL":
            print("\n⚠ CRITICAL: Stop agents and resolve before continuing.")
            sys.exit(1)

    elif args.command == "read":
        if args.new:
            messages = read_new(args.channel)
        elif args.since is not None:
            messages = read_since(args.channel, args.since)
        else:
            # Read all
            messages = read_since(args.channel, 0)
        if messages:
            _print_messages(messages)
        else:
            print("  (no new messages)")

    elif args.command == "wait":
        if not args.channel and not args.wait_any:
            print("Error: specify a channel or --any", file=sys.stderr)
            sys.exit(1)

        if args.wait_any:
            result = wait_any(timeout=args.timeout)
            if result:
                ch, msg = result
                print(f"  {ch}: {msg.msg_type} — {msg.message}")
                if msg.metrics:
                    print(f"  metrics: {json.dumps(msg.metrics)}")
                sys.exit(0 if msg.msg_type == "COMPLETE" else 1)
            else:
                print(f"  Timeout after {args.timeout}s — no channel finished.")
                sys.exit(2)
        else:
            msg = wait_channel(args.channel, timeout=args.timeout, on_step=args.on_step)
            if msg:
                print(f"  {args.channel}: {msg.step} — {msg.msg_type} — {msg.message}")
                if msg.metrics:
                    print(f"  metrics: {json.dumps(msg.metrics)}")
                # Exit 0 for COMPLETE or step transitions (on_step mode)
                sys.exit(0 if msg.msg_type in ("COMPLETE", "PROGRESS", "TICK") else 1)
            else:
                print(f"  Timeout after {args.timeout}s — {args.channel} still running.")
                sys.exit(2)

    elif args.command == "monitor":
        deadline = time.monotonic() + args.timeout
        poll_num = 0
        while time.monotonic() < deadline:
            poll_num += 1
            print(f"\n{'#' * 70}")
            print(f"  MONITOR POLL #{poll_num}  (every {args.interval}s, channel: {args.channel})")
            print(f"{'#' * 70}")
            overall = print_status()

            # Check if target channel is done
            msg = read_latest(args.channel)
            if msg and msg.msg_type in _TERMINAL_TYPES:
                print(f"\n  >>> AGENT FINISHED: {msg.msg_type} — {msg.message}")
                if msg.metrics:
                    print(f"  >>> metrics: {json.dumps(msg.metrics)}")
                sys.exit(0 if msg.msg_type == "COMPLETE" else 1)

            if overall == "CRITICAL":
                print(f"\n  >>> CRITICAL: System health problem detected. Continuing to monitor...")

            # Show new messages since last poll
            new_msgs = read_new(args.channel)
            if new_msgs:
                print(f"  New messages ({len(new_msgs)}):")
                _print_messages(new_msgs)

            sys.stdout.flush()
            remaining = deadline - time.monotonic()
            sleep_time = min(args.interval, remaining)
            if sleep_time > 0:
                time.sleep(sleep_time)

        print(f"\n  Monitor timeout after {args.timeout}s — agent still running.")
        sys.exit(2)

    elif args.command == "clean":
        if args.channel:
            clean_channel(args.channel)
        else:
            clean_all()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
