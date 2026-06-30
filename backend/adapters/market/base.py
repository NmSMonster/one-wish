"""Bazowy interfejs źródła danych + adapter publikujący ticki na szynę.

`MarketSource` to dowolne źródło ticków (Binance live, replay, syntetyk).
`MarketDataAdapter` opakowuje źródło, publikuje MARKET_TICK i pilnuje świeżości
danych (DATA_LAG_WARNING / STALE_FEED) — bot nie ufa nieświeżym danym.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ...core.bus import EventBus
from ...core.clock import Clock, RealClock
from ...core.events import Event, EventType, Severity
from ...core.types import MarketTick

log = logging.getLogger("onewish.market")


class MarketSource(ABC):
    """Źródło strumienia MarketTick."""

    @abstractmethod
    def ticks(self) -> AsyncIterator[MarketTick]:
        """Asynchroniczny generator ticków."""
        raise NotImplementedError


class MarketDataAdapter:
    SOURCE = "market_data_adapter"

    def __init__(
        self,
        source: MarketSource,
        bus: EventBus,
        clock: Clock | None = None,
        *,
        lag_warn_ms: float = 1000.0,
        stale_after_s: float = 5.0,
    ) -> None:
        self._source = source
        self._bus = bus
        self._clock = clock or RealClock()
        self._lag_warn_ms = lag_warn_ms
        self._stale_after_s = stale_after_s
        self._last_ts: float | None = None

    async def run(self, max_ticks: int | None = None) -> int:
        """Pompuje ticki ze źródła na szynę. Zwraca liczbę opublikowanych ticków."""
        n = 0
        async for tick in self._source.ticks():
            await self._bus.publish(
                Event(EventType.MARKET_TICK, tick.ts, self.SOURCE, payload=tick)
            )

            if tick.data_lag_ms >= self._lag_warn_ms:
                await self._bus.publish(
                    Event(
                        EventType.DATA_LAG_WARNING,
                        tick.ts,
                        self.SOURCE,
                        Severity.WARNING,
                        payload={"asset": tick.asset.value, "data_lag_ms": tick.data_lag_ms},
                    )
                )

            # Luka czasowa między tickami w danych = potencjalnie zamrożony feed.
            if self._last_ts is not None and (tick.ts - self._last_ts) > self._stale_after_s:
                await self._bus.publish(
                    Event(
                        EventType.STALE_FEED,
                        tick.ts,
                        self.SOURCE,
                        Severity.WARNING,
                        payload={"gap_s": tick.ts - self._last_ts},
                    )
                )
            self._last_ts = tick.ts

            n += 1
            if max_ticks is not None and n >= max_ticks:
                break
        return n
