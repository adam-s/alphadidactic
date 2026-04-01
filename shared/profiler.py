"""
Experiment Profiler — Wrap experiment execution to capture performance metrics.

Measures wall time, CPU, memory, DB query patterns, and I/O for each pipeline
step. Produces a structured performance report that the orchestrator uses to
identify bottlenecks and plan optimizations.

Usage as context manager:
    from shared.profiler import ExperimentProfiler

    profiler = ExperimentProfiler("08_extrinsic_flow")

    with profiler.step("STEP_1_DATA"):
        run_build_dataset()

    with profiler.step("STEP_2_STRATEGY"):
        run_strategy()

    profiler.report()  # prints summary + writes PERFORMANCE.md

Usage as CLI wrapper:
    python -m shared.profiler run experiments/08_extrinsic_flow/build_dataset.py
    python -m shared.profiler report experiments/08_extrinsic_flow/
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO


# ═══════════════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResourceReading:
    """Single point-in-time resource reading."""
    timestamp: float  # time.monotonic()
    wall_clock: str   # ISO format
    cpu_pct: float
    memory_rss_mb: float
    disk_free_gb: float
    db_connections: int
    db_active_queries: int


@dataclass
class StepProfile:
    """Performance profile for one pipeline step."""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    wall_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    avg_cpu_pct: float = 0.0
    peak_cpu_pct: float = 0.0
    disk_delta_mb: float = 0.0
    readings: list[ResourceReading] = field(default_factory=list)
    error: str | None = None

    @property
    def wall_minutes(self) -> float:
        return self.wall_seconds / 60


@dataclass
class ExperimentProfile:
    """Full performance profile for an experiment run."""
    experiment: str
    start_wall: str = ""
    end_wall: str = ""
    total_seconds: float = 0.0
    steps: list[StepProfile] = field(default_factory=list)
    bottleneck: str = ""
    recommendations: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Resource Sampling
# ═══════════════════════════════════════════════════════════════════════════════


def _get_cpu_pct() -> float:
    """Get current process tree CPU usage."""
    try:
        result = subprocess.run(
            ["ps", "-o", "%cpu", "-p", str(os.getpid())],
            capture_output=True, text=True, timeout=2
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            return float(lines[1].strip())
    except Exception:
        pass
    return 0.0


def _get_memory_rss_mb() -> float:
    """Get current process RSS in MB."""
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return ru.ru_maxrss / (1024 * 1024)  # macOS reports in bytes
    except Exception:
        return 0.0


def _get_disk_free_gb() -> float:
    try:
        _, _, free = shutil.disk_usage("/")
        return free / (1024 ** 3)
    except Exception:
        return 0.0


def _get_db_stats() -> tuple[int, int]:
    """Get DB connection count and active queries."""
    try:
        from shared.db_monitor import safe_connection, get_connection_count, get_active_queries
        with safe_connection(statement_timeout="5s") as conn:
            count = get_connection_count(conn)
            queries = get_active_queries(conn)
            return count, len(queries)
    except Exception:
        return 0, 0


def _sample() -> ResourceReading:
    db_conn, db_active = _get_db_stats()
    return ResourceReading(
        timestamp=time.monotonic(),
        wall_clock=datetime.now().isoformat(timespec="seconds"),
        cpu_pct=_get_cpu_pct(),
        memory_rss_mb=_get_memory_rss_mb(),
        disk_free_gb=_get_disk_free_gb(),
        db_connections=db_conn,
        db_active_queries=db_active,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Profiler
# ═══════════════════════════════════════════════════════════════════════════════


class ExperimentProfiler:
    """Profile an experiment's resource usage across pipeline steps.

    Samples resources at configurable intervals during each step.
    Produces a structured report with bottleneck analysis.
    """

    def __init__(self, experiment: str, sample_interval: float = 5.0):
        self.experiment = experiment
        self.sample_interval = sample_interval
        self.profile = ExperimentProfile(experiment=experiment)
        self._start = time.monotonic()
        self.profile.start_wall = datetime.now().isoformat(timespec="seconds")

    @contextmanager
    def step(self, name: str):
        """Profile a pipeline step."""
        sp = StepProfile(name=name, start_time=time.monotonic())
        disk_before = _get_disk_free_gb()

        # Take initial sample
        sp.readings.append(_sample())

        try:
            yield sp
        except Exception as e:
            sp.error = str(e)
            raise
        finally:
            # Take final sample
            sp.readings.append(_sample())

            sp.end_time = time.monotonic()
            sp.wall_seconds = sp.end_time - sp.start_time
            disk_after = _get_disk_free_gb()
            sp.disk_delta_mb = (disk_before - disk_after) * 1024  # GB → MB

            if sp.readings:
                cpu_vals = [r.cpu_pct for r in sp.readings if r.cpu_pct > 0]
                mem_vals = [r.memory_rss_mb for r in sp.readings]
                sp.avg_cpu_pct = sum(cpu_vals) / len(cpu_vals) if cpu_vals else 0
                sp.peak_cpu_pct = max(cpu_vals) if cpu_vals else 0
                sp.peak_memory_mb = max(mem_vals) if mem_vals else 0

            self.profile.steps.append(sp)

    def _analyze_bottleneck(self):
        """Identify the slowest step and classify the bottleneck."""
        if not self.profile.steps:
            return

        slowest = max(self.profile.steps, key=lambda s: s.wall_seconds)
        self.profile.bottleneck = slowest.name

        recs = []

        # Time analysis
        total = sum(s.wall_seconds for s in self.profile.steps)
        if total > 0:
            pct = slowest.wall_seconds / total * 100
            recs.append(f"{slowest.name} is {pct:.0f}% of total time ({slowest.wall_minutes:.1f} min)")

        # CPU analysis
        if slowest.avg_cpu_pct < 30 and slowest.wall_seconds > 60:
            recs.append(f"{slowest.name}: Low CPU ({slowest.avg_cpu_pct:.0f}%) — likely I/O bound (DB queries)")
            recs.append("Consider: parallelize day-by-day queries across symbols")
        elif slowest.peak_cpu_pct > 90:
            recs.append(f"{slowest.name}: High CPU ({slowest.peak_cpu_pct:.0f}%) — compute bound")
            recs.append("Consider: vectorize with numpy, reduce rolling window recomputation")

        # Memory analysis
        peak_mem = max(s.peak_memory_mb for s in self.profile.steps) if self.profile.steps else 0
        if peak_mem > 2000:
            recs.append(f"Peak memory: {peak_mem:.0f} MB — consider streaming instead of loading full dataset")

        # Disk analysis
        total_disk = sum(s.disk_delta_mb for s in self.profile.steps)
        if total_disk > 500:
            recs.append(f"Disk usage: {total_disk:.0f} MB written — consider compression or smaller output")

        # DB analysis
        max_db = max(
            (max(r.db_active_queries for r in s.readings) if s.readings else 0)
            for s in self.profile.steps
        )
        if max_db > 2:
            recs.append(f"Peak concurrent DB queries: {max_db} — close to 4-connection limit")

        self.profile.recommendations = recs

    def report(self, output_dir: Path | None = None) -> str:
        """Generate and return the performance report. Optionally write to PERFORMANCE.md."""
        self.profile.end_wall = datetime.now().isoformat(timespec="seconds")
        self.profile.total_seconds = time.monotonic() - self._start
        self._analyze_bottleneck()

        lines = [
            f"# Performance Report: {self.experiment}",
            "",
            f"**Total time:** {self.profile.total_seconds / 60:.1f} minutes",
            f"**Started:** {self.profile.start_wall}",
            f"**Finished:** {self.profile.end_wall}",
            f"**Bottleneck:** {self.profile.bottleneck}",
            "",
            "## Step Breakdown",
            "",
            "| Step | Wall Time | CPU avg | CPU peak | Memory peak | Disk delta | Status |",
            "|------|-----------|---------|----------|-------------|------------|--------|",
        ]

        for s in self.profile.steps:
            status = "ERROR" if s.error else "OK"
            lines.append(
                f"| {s.name} | {s.wall_minutes:.1f} min | {s.avg_cpu_pct:.0f}% | "
                f"{s.peak_cpu_pct:.0f}% | {s.peak_memory_mb:.0f} MB | "
                f"{s.disk_delta_mb:+.0f} MB | {status} |"
            )

        lines.extend([
            "",
            "## Recommendations",
            "",
        ])
        for rec in self.profile.recommendations:
            lines.append(f"- {rec}")

        if not self.profile.recommendations:
            lines.append("- No optimization recommendations (all steps within normal bounds)")

        lines.extend([
            "",
            "## Raw Profile",
            "",
            "```json",
            json.dumps(asdict(self.profile), indent=2, default=str),
            "```",
        ])

        report_text = "\n".join(lines)

        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "PERFORMANCE.md").write_text(report_text)

        return report_text

    def print_report(self, output_dir: Path | None = None):
        """Print and optionally save the report."""
        print(self.report(output_dir))


# ═══════════════════════════════════════════════════════════════════════════════
# CLI — Profile a script execution
# ═══════════════════════════════════════════════════════════════════════════════


def profile_script(script_path: str, experiment_dir: str | None = None) -> StepProfile:
    """Run a Python script and profile its execution."""
    script = Path(script_path)
    exp_dir = Path(experiment_dir) if experiment_dir else script.parent
    step_name = script.stem

    profiler = ExperimentProfiler(exp_dir.name)

    with profiler.step(step_name) as sp:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(exp_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            sp.error = result.stderr[-500:] if result.stderr else f"exit code {result.returncode}"
            print(f"STDERR:\n{result.stderr}", file=sys.stderr)

    profiler.print_report(exp_dir)
    return profiler.profile.steps[0]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Experiment performance profiler")
    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="Profile a script execution")
    run_cmd.add_argument("script", help="Path to Python script")
    run_cmd.add_argument("--dir", help="Experiment directory (default: script's parent)")

    report_cmd = sub.add_parser("report", help="View a saved performance report")
    report_cmd.add_argument("dir", help="Experiment directory containing PERFORMANCE.md")

    args = parser.parse_args()

    if args.command == "run":
        profile_script(args.script, args.dir)

    elif args.command == "report":
        perf_path = Path(args.dir) / "PERFORMANCE.md"
        if perf_path.exists():
            print(perf_path.read_text())
        else:
            print(f"No PERFORMANCE.md found in {args.dir}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
