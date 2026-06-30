"""ReplaySource — odtwarza wcześniej zebrane/zapisane ticki.

Używane przez backtest (M10) i testy: deterministyczne, bez sieci.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

from ...core.clock import Clock
from ...core.types import MarketTick
from .base import MarketSource


class ReplaySource(MarketSource):
    def __init__(
        self,
        ticks: Iterable[MarketTick],
        *,
        clock: Clock | None = None,
        pace: bool = False,
    ) -> None:
        self._ticks = list(ticks)
        self._clock = clock
        self._pace = pace

    async def ticks(self) -> AsyncIterator[MarketTick]:
        prev_ts: float | None = None
        for tick in self._ticks:
            if self._pace and prev_ts is not None:
                await asyncio.sleep(max(0.0, tick.ts - prev_ts))
            prev_ts = tick.ts
            yield tick
