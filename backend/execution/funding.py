"""FundingAccrual — naliczanie funding przy rozliczeniach (strukturalny edge carry).

Gdy strumień ticków przekracza moment rozliczenia funding (next_funding_ts skacze
do następnego cyklu), naliczamy funding otwartym pozycjom. Dla pary delta-neutral
z short perp przy DODATNIM funding short OTRZYMUJE funding — to główny, powtarzalny
edge strategii basis/funding, którego scalp na samej dyslokacji nie łapie.

Cashflow funding dla nogi perp o wielkości q (ze znakiem; q<0 = short):
    funding = -q * funding_rate * perp_mark
short (q<0) przy rate>0 → dodatni (otrzymuje); long (q>0) → płaci.
"""
from __future__ import annotations

import logging

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import MarketTick
from .book import PositionBook

log = logging.getLogger("onewish.funding")


class FundingAccrual:
    SOURCE = "funding_accrual"

    def __init__(self, book: PositionBook, bus: EventBus | None = None) -> None:
        self.book = book
        self._bus = bus
        self._last_next_funding: dict = {}
        self._marks: dict = {}

    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick):
            return
        self._marks[tick.asset] = (tick.spot, tick.perp)

        prev = self._last_next_funding.get(tick.asset)
        self._last_next_funding[tick.asset] = tick.next_funding_ts
        if prev is None or tick.next_funding_ts <= prev:
            return  # brak przekroczenia rozliczenia

        pos = self.book.position(tick.asset)
        if pos is None or not pos.is_open or abs(pos.perp_qty) < 1e-12:
            return

        funding = -pos.perp_qty * tick.funding_rate * tick.perp
        self.book.add_funding(tick.asset, funding)
        if self._bus is not None:
            await self._bus.publish(Event(
                EventType.FUNDING_ACCRUED, tick.ts, self.SOURCE,
                payload={"asset": tick.asset.value, "amount": funding,
                         "funding_rate": tick.funding_rate}))
            await self._bus.publish(Event(
                EventType.PNL_UPDATE, tick.ts, self.SOURCE,
                payload=self.book.snapshot(self._marks, tick.ts)))
