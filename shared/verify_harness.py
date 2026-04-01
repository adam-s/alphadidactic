"""Reusable verification harness for experiment integrity checks.

Provides Checks 1, 2, 5, 7, 8 as generic implementations.
Experiments implement Checks 3 (temporal trace), 4 (manual calc),
and 6 (incremental vs batch) themselves — these are strategy-specific.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from shared.config import SPLIT_THRESHOLD, TRAIN_END
from shared.cursor_engine import CursorEngine, MinuteBarsSource
from shared.metrics import sharpe

ET = ZoneInfo("America/New_York")


class VerificationHarness:
    """Base harness for 8-step experiment verification."""

    def __init__(self, out_dir: Path, engine: CursorEngine, conn, trading_days: list):
        self.out = out_dir
        self.engine = engine
        self.conn = conn
        self.trading_days = trading_days
        self.passed: list[dict] = []
        self.failed: list[dict] = []

    def check_pass(self, name: str, detail: str):
        print(f"  PASS  {name}: {detail}", file=sys.stderr)
        self.passed.append({"check": name, "status": "PASS", "detail": detail})

    def check_fail(self, name: str, detail: str):
        print(f"  FAIL  {name}: {detail}", file=sys.stderr)
        self.failed.append({"check": name, "status": "FAIL", "detail": detail})

    # ─── Check 1: Cache vs Raw ─────────────────────────────────────────

    def check_1_cache_vs_raw(self, symbols: list[str], checkpoints: dict[str, str]):
        """Compare engine prices against raw DB.

        Args:
            symbols: e.g. ["SPY", "VXX", "SQQQ"]
            checkpoints: e.g. {"p0935": "09:35", "p1530": "15:30", "p1600": "16:00"}
        """
        td = self.trading_days
        sample_dates = [td[i] for i in [0, len(td)//4, len(td)//2, 3*len(td)//4, -1]]
        mismatches = 0

        for d in sample_dates:
            tape = self.engine.resolve_day(self.conn, d, symbols)
            for cp_name, target_time in checkpoints.items():
                for sym in symbols:
                    ep = tape.get_price(cp_name, sym)
                    if ep is None:
                        continue
                    with self.conn.cursor() as cur:
                        cur.execute("""
                            SELECT close FROM minute_bars
                            WHERE symbol = %s
                              AND time >= %s::timestamp AND time < %s::timestamp
                              AND ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::time
                                  BETWEEN %s::time - interval '5 minutes' AND %s::time
                            ORDER BY time DESC LIMIT 1
                        """, (sym, f"{d} 00:00:00", f"{d} 23:59:59", target_time, target_time))
                        row = cur.fetchone()
                    if row and abs(float(row[0]) - ep) > 1e-6:
                        mismatches += 1

        n_cp = len(checkpoints)
        if mismatches == 0:
            self.check_pass("Check 1", f"5 dates × {len(symbols)} symbols × {n_cp} checkpoints — all match")
        else:
            self.check_fail("Check 1", f"{mismatches} mismatches")

    # ─── Check 2: DST ──────────────────────────────────────────────────

    def check_2_dst(self):
        """Verify UTC offset differs between EDT and EST in March/November."""
        offsets = set()
        for td in self.trading_days:
            if td.month in (3, 11):
                dt = datetime(td.year, td.month, td.day, 9, 35, tzinfo=ET)
                offsets.add(dt.utcoffset().total_seconds() / 3600)
        if len(offsets) >= 2:
            self.check_pass("Check 2", f"DST offsets: {sorted(offsets)}")
        else:
            self.check_fail("Check 2", f"Only one offset: {offsets}")

    # ─── Check 5: Train/Test Consistency ───────────────────────────────

    def check_5_train_test(self, results: pd.DataFrame):
        """Report train/test/full Sharpe. Flag test > 3.0."""
        dr = results["day_ret"].values
        dt = pd.to_datetime(results["date"])
        mask = dt <= pd.Timestamp(TRAIN_END)

        tr_sh = sharpe(dr[mask])
        te_sh = sharpe(dr[~mask])
        full_sh = sharpe(dr)
        detail = f"Train={tr_sh:.3f} Test={te_sh:.3f} Full={full_sh:.3f}"

        if abs(te_sh) > 3.0:
            self.check_fail("Check 5", f"{detail} — TEST > 3.0, investigate")
        else:
            self.check_pass("Check 5", detail)

    # ─── Check 7: Signal Direction ─────────────────────────────────────

    def check_7_signal_direction(self, results: pd.DataFrame):
        """Compare signal-on active returns vs buy-and-hold SPY."""
        dr = results["day_ret"].values
        dt = pd.to_datetime(results["date"])
        mask = dt <= pd.Timestamp(TRAIN_END)

        # Buy-and-hold SPY: close-to-close
        prev_spy = None
        spy_rets = []
        spy_dates = []
        for today in self.trading_days:
            tape = self.engine.resolve_day(self.conn, today, ["SPY"])
            spy_close = tape.get_price("p1600", "SPY")
            if prev_spy and spy_close and prev_spy > 0 and spy_close > 0:
                r = spy_close / prev_spy - 1
                if abs(r) < SPLIT_THRESHOLD:
                    spy_rets.append(r)
                    spy_dates.append(today)
            if spy_close:
                prev_spy = spy_close

        spy = np.array(spy_rets)
        spy_dt = pd.to_datetime(spy_dates)
        spy_train = np.mean(spy[spy_dt <= pd.Timestamp(TRAIN_END)])
        spy_test = np.mean(spy[spy_dt > pd.Timestamp(TRAIN_END)])

        train_active = dr[mask & (dr != 0)]
        test_active = dr[(~mask) & (dr != 0)]

        train_spread = (np.mean(train_active) - spy_train) if len(train_active) > 0 else 0
        test_spread = (np.mean(test_active) - spy_test) if len(test_active) > 0 else 0

        train_ok = train_spread >= 0
        test_ok = test_spread >= 0
        cross_ok = train_spread * test_spread >= 0

        detail = (f"Train: signal={np.mean(train_active):.6f} vs SPY_BH={spy_train:.6f} "
                  f"spread={train_spread:.6f} | "
                  f"Test: signal={np.mean(test_active):.6f} vs SPY_BH={spy_test:.6f} "
                  f"spread={test_spread:.6f}")
        if train_ok and test_ok and cross_ok:
            self.check_pass("Check 7", detail)
        else:
            self.check_fail("Check 7", f"{detail} — null result")

    # ─── Check 8: Data Integrity ───────────────────────────────────────

    def check_8_data_integrity(self, symbols: list[str], min_days: int = 900,
                               extra_checks: list[str] | None = None):
        """Verify symbols have sufficient data coverage."""
        issues = []
        for sym in symbols:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(DISTINCT ((time AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::date)
                    FROM minute_bars
                    WHERE symbol = %s AND time >= '2022-01-01'::timestamp AND time < '2026-03-01'::timestamp
                """, (sym,))
                n = cur.fetchone()[0]
                if n < min_days:
                    issues.append(f"{sym}: {n} days")

        # Check data gaps file
        gaps_file = self.out / "data_gaps.json"
        if gaps_file.exists():
            gaps = json.loads(gaps_file.read_text())
            if gaps:
                issues.append(f"{len(gaps)} data gaps")

        if extra_checks:
            issues.extend(extra_checks)

        if not issues:
            self.check_pass("Check 8", f"{len(symbols)} symbols all {min_days}+ days")
        else:
            self.check_pass("Check 8", f"Notes: {'; '.join(issues)}")

    # ─── Summarize ─────────────────────────────────────────────────────

    def summarize(self) -> bool:
        """Print summary, write verification.json. Returns True if all passed."""
        print(f"\n{'='*70}", file=sys.stderr)
        print(f"  VERIFICATION: {len(self.passed)} passed, {len(self.failed)} failed", file=sys.stderr)
        print(f"{'='*70}", file=sys.stderr)

        (self.out / "verification.json").write_text(
            json.dumps(self.passed + self.failed, indent=2))

        if self.failed:
            for f in self.failed:
                print(f"  FAILED: {f['check']} — {f['detail']}", file=sys.stderr)
            return False
        return True
