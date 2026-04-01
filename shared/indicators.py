"""Reusable online indicators for strategy loops.

These are optional tools — experiments can use them or define their own.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np


class OnlineEMA:
    """Exponential moving average updated one observation at a time.

    Suppresses output during warmup (first min_obs observations) to avoid
    anchoring the EMA to a single initial value.
    """

    def __init__(self, span: int, min_obs: int = 5):
        self.alpha = 2.0 / (span + 1)
        self.value: float | None = None
        self.n = 0
        self.min_obs = min_obs

    def update(self, x: float):
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1 - self.alpha) * self.value
        self.n += 1

    def get(self) -> float | None:
        return self.value if self.n >= self.min_obs else None


class Accumulator:
    """Rolling hit-rate, average positive return, and win-streak tracker.

    Used for cross-sectional signal ranking in overnight momentum strategies.
    """

    def __init__(self, lookback: int = 80):
        self.lookback = lookback
        self.rets: dict[str, list[float]] = defaultdict(list)
        self.hit_rate: dict[str, float] = {}
        self.avg_pos: dict[str, float] = {}
        self.streak: dict[str, int] = {}

    def update(self, sym: str, ret: float):
        self.rets[sym].append(ret)
        r = self.rets[sym]
        if len(r) < 20:
            self.hit_rate.pop(sym, None)
            self.avg_pos.pop(sym, None)
            self.streak[sym] = 0
            return
        recent = r[-self.lookback:]
        pos = [x for x in recent if x > 0]
        self.hit_rate[sym] = len(pos) / len(recent)
        self.avg_pos[sym] = float(np.mean(pos)) if pos else 0.0
        s = 0
        for x in reversed(r):
            if x > 0:
                s += 1
            else:
                break
        self.streak[sym] = s

    def get_signal(self, sym: str, iret: float, streak_mult: float) -> float | None:
        if sym not in self.hit_rate:
            return None
        return iret * self.avg_pos.get(sym, 0.0) * (1 + streak_mult * self.streak.get(sym, 0)) * self.hit_rate[sym]
