"""
System Monitor — Disk, memory, CPU, and Docker health checks.

Prints a compact status table to console. Designed to run before and during
experiments to catch resource exhaustion before it kills everything.

Usage:
    python -m shared.system_monitor              # one-shot status
    python -m shared.system_monitor watch        # continuous (every 30s)
    python -m shared.system_monitor watch --interval 10
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# Thresholds
# ═══════════════════════════════════════════════════════════════════════════════

DISK_WARN_GB = 30       # warn below this
DISK_CRITICAL_GB = 15   # STOP below this
MEM_WARN_PCT = 85       # warn above this
MEM_CRITICAL_PCT = 95   # STOP above this


# ═══════════════════════════════════════════════════════════════════════════════
# Data Collection
# ═══════════════════════════════════════════════════════════════════════════════


def get_disk_usage() -> dict:
    total, used, free = shutil.disk_usage("/")
    total_gb = total / (1024 ** 3)
    used_gb = used / (1024 ** 3)
    free_gb = free / (1024 ** 3)
    pct = (used / total) * 100
    if free_gb < DISK_CRITICAL_GB:
        status = "CRITICAL"
    elif free_gb < DISK_WARN_GB:
        status = "WARNING"
    else:
        status = "OK"
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "pct": pct,
        "status": status,
    }


def get_memory_usage() -> dict:
    try:
        result = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        stats = {}
        for line in lines[1:]:
            if ":" in line:
                key, val = line.split(":", 1)
                val = val.strip().rstrip(".")
                try:
                    stats[key.strip()] = int(val)
                except ValueError:
                    pass

        page_size = 16384  # macOS default
        free = stats.get("Pages free", 0) * page_size
        active = stats.get("Pages active", 0) * page_size
        inactive = stats.get("Pages inactive", 0) * page_size
        speculative = stats.get("Pages speculative", 0) * page_size
        wired = stats.get("Pages wired down", 0) * page_size
        compressed = stats.get("Pages occupied by compressor", 0) * page_size

        total_bytes = (free + active + inactive + speculative + wired + compressed)
        used_bytes = active + wired + compressed
        total_gb = total_bytes / (1024 ** 3)
        used_gb = used_bytes / (1024 ** 3)
        pct = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0

        if pct > MEM_CRITICAL_PCT:
            status = "CRITICAL"
        elif pct > MEM_WARN_PCT:
            status = "WARNING"
        else:
            status = "OK"

        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": total_gb - used_gb,
            "pct": pct,
            "status": status,
        }
    except Exception as e:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "pct": 0, "status": f"ERROR: {e}"}


def get_cpu_usage() -> dict:
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "%cpu"],
            capture_output=True, text=True, timeout=5
        )
        total = sum(float(line.strip()) for line in result.stdout.strip().split("\n")[1:] if line.strip())
        cores = os.cpu_count() or 1
        pct = total / cores
        status = "WARNING" if pct > 80 else "OK"
        return {"pct": pct, "cores": cores, "status": status}
    except Exception as e:
        return {"pct": 0, "cores": 0, "status": f"ERROR: {e}"}


def get_docker_usage() -> dict:
    try:
        result = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}\t{{.Reclaimable}}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"status": "NOT RUNNING"}

        lines = result.stdout.strip().split("\n")
        info = {}
        for line in lines:
            parts = line.split("\t")
            if len(parts) >= 3:
                info[parts[0]] = {"size": parts[1], "reclaimable": parts[2]}

        # Check postgres container
        pg_result = subprocess.run(
            ["docker", "ps", "--filter", "name=postgres", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        pg_status = pg_result.stdout.strip() if pg_result.stdout.strip() else "NOT RUNNING"

        return {
            "images": info.get("Images", {}).get("size", "?"),
            "images_reclaimable": info.get("Images", {}).get("reclaimable", "?"),
            "volumes": info.get("Local Volumes", {}).get("size", "?"),
            "build_cache": info.get("Build Cache", {}).get("size", "?"),
            "build_cache_reclaimable": info.get("Build Cache", {}).get("reclaimable", "?"),
            "postgres": pg_status,
            "status": "OK",
        }
    except FileNotFoundError:
        return {"status": "NOT INSTALLED"}
    except Exception as e:
        return {"status": f"ERROR: {e}"}


def get_db_connections() -> dict:
    try:
        from shared.db_monitor import safe_connection, get_connection_count, get_active_queries
        with safe_connection(statement_timeout="5s") as conn:
            count = get_connection_count(conn)
            queries = get_active_queries(conn)
            long = [q for q in queries if q.duration_seconds > 30]
            return {
                "connections": count,
                "active_queries": len(queries),
                "long_queries": len(long),
                "status": "CRITICAL" if long else ("WARNING" if count > 3 else "OK"),
            }
    except Exception:
        return {"connections": 0, "active_queries": 0, "long_queries": 0, "status": "DISCONNECTED"}


# ═══════════════════════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════════════════════


def status_icon(status: str) -> str:
    if status == "OK":
        return " OK "
    elif status == "WARNING":
        return "WARN"
    elif status == "CRITICAL":
        return "CRIT"
    else:
        return " ?? "


def print_status_table():
    disk = get_disk_usage()
    mem = get_memory_usage()
    cpu = get_cpu_usage()
    docker = get_docker_usage()
    db = get_db_connections()

    has_critical = any(
        d.get("status") == "CRITICAL"
        for d in [disk, mem, cpu, db]
    )

    print()
    print("=" * 62)
    print(f"  SYSTEM STATUS  {'CRITICAL - STOP' if has_critical else 'OK'}")
    print("=" * 62)
    print(f"  {'Resource':<22} {'Value':>18} {'Free/Avail':>10}  {'Status':>4}")
    print("-" * 62)

    # Disk
    print(f"  {'Disk':<22} {disk['used_gb']:>7.1f} / {disk['total_gb']:.0f} GB  {disk['free_gb']:>7.1f} GB  [{status_icon(disk['status'])}]")

    # Memory
    print(f"  {'Memory':<22} {mem['used_gb']:>7.1f} / {mem['total_gb']:.0f} GB  {mem['free_gb']:>7.1f} GB  [{status_icon(mem['status'])}]")

    # CPU
    cpu_label = f"CPU ({cpu['cores']} cores)"
    print(f"  {cpu_label:<22} {cpu['pct']:>17.0f}%  {'':>10}  [{status_icon(cpu['status'])}]")

    # Docker
    if docker.get("status") not in ("NOT RUNNING", "NOT INSTALLED"):
        print("-" * 62)
        print(f"  {'Docker Images':<22} {docker.get('images', '?'):>18}  {'reclaim: ' + docker.get('images_reclaimable', '?'):>10}")
        print(f"  {'Docker Volumes':<22} {docker.get('volumes', '?'):>18}")
        print(f"  {'Docker Build Cache':<22} {docker.get('build_cache', '?'):>18}  {'reclaim: ' + docker.get('build_cache_reclaimable', '?'):>10}")
        print(f"  {'Postgres':<22} {docker.get('postgres', '?'):>18}")

    # DB
    print("-" * 62)
    print(f"  {'DB Connections':<22} {db['connections']:>18}  {'':>10}  [{status_icon(db['status'])}]")
    if db['active_queries'] > 0:
        print(f"  {'  Active Queries':<22} {db['active_queries']:>18}")
    if db['long_queries'] > 0:
        print(f"  {'  Long Queries (>30s)':<22} {db['long_queries']:>18}  {'':>10}  [CRIT]")

    print("=" * 62)

    if has_critical:
        print()
        print("  *** CRITICAL ISSUES DETECTED ***")
        if disk["status"] == "CRITICAL":
            print(f"  DISK: Only {disk['free_gb']:.1f} GB free! Below {DISK_CRITICAL_GB} GB threshold.")
            print(f"         Run: docker system prune -f && docker builder prune -f")
        if mem["status"] == "CRITICAL":
            print(f"  MEMORY: {mem['pct']:.0f}% used! Above {MEM_CRITICAL_PCT}% threshold.")
        if db.get("status") == "CRITICAL":
            print(f"  DB: {db['long_queries']} queries running >30s!")
            print(f"      Run: python -m shared.db_monitor kill")
        print()
        return False  # signal to caller: do not proceed

    if disk["status"] == "WARNING":
        print()
        print(f"  NOTE: Disk is low ({disk['free_gb']:.1f} GB free).")
        print(f"  Consider: docker system prune -f && docker builder prune -f")
        print()

    return True  # OK to proceed


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


def write_to_apc(channel_id: str = "system_monitor"):
    """Write system health snapshots to APC channel continuously."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from shared.agent_protocol import AgentChannel
    except ImportError:
        print("APC not available, falling back to console only", file=sys.stderr)
        return

    channel = AgentChannel(channel_id, "system")

    while True:
        try:
            # Recreate channel directory if it was cleaned while we're running
            channel.channel_dir.mkdir(parents=True, exist_ok=True)

            disk = get_disk_usage()
            mem = get_memory_usage()
            cpu = get_cpu_usage()
            db = get_db_connections()

            has_critical = any(
                d.get("status") == "CRITICAL"
                for d in [disk, mem, cpu, db]
            )

            metrics = {
                "disk_free_gb": round(disk["free_gb"], 1),
                "disk_pct": round(disk["pct"], 1),
                "mem_used_pct": round(mem["pct"], 1),
                "cpu_pct": round(cpu["pct"], 0),
                "db_connections": db["connections"],
                "db_active_queries": db["active_queries"],
            }

            if has_critical:
                details = []
                if disk["status"] == "CRITICAL":
                    details.append(f"DISK {disk['free_gb']:.0f}GB free")
                if mem["status"] == "CRITICAL":
                    details.append(f"MEM {mem['pct']:.0f}%")
                if db.get("status") == "CRITICAL":
                    details.append(f"DB {db.get('long_queries', 0)} long queries")
                channel._write("WARNING", "SYSTEM", f"CRITICAL: {', '.join(details)}", metrics)
            else:
                channel.heartbeat("SYSTEM", f"OK: disk {disk['free_gb']:.0f}GB, mem {mem['pct']:.0f}%, cpu {cpu['pct']:.0f}%")
        except Exception as e:
            print(f"Monitor error (will retry): {e}", file=sys.stderr)

        time.sleep(30)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="System health monitor")
    sub = parser.add_subparsers(dest="command")

    watch_cmd = sub.add_parser("watch", help="Continuous monitoring (console)")
    watch_cmd.add_argument("--interval", type=int, default=30)

    sub.add_parser("apc", help="Write health snapshots to APC channel continuously")

    args = parser.parse_args()

    if args.command == "watch":
        while True:
            os.system("clear")
            ok = print_status_table()
            if not ok:
                print("  Monitoring paused — resolve critical issues before continuing.")
            time.sleep(args.interval)
    elif args.command == "apc":
        write_to_apc()
    else:
        print_status_table()


if __name__ == "__main__":
    main()
