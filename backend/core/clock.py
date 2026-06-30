"""Zegar — abstrakcja czasu.

RealClock dla trybu live, SimClock dla testów/backtestu (deterministyczny replay
sekunda po sekundzie). Cały kod używa Clock zamiast time.time(), żeby backtest
i testy były powtarzalne.
"""
from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        ...


class RealClock:
    def now(self) -> float:
        return time.time()


class SimClock:
    """Zegar symulowany — ręcznie ustawiany / przesuwany."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def set(self, t: float) -> None:
        self._t = float(t)

    def advance(self, dt: float) -> float:
        self._t += float(dt)
        return self._t
