"""ExecutionEngine (M7) — cienka warstwa zdarzeniowa nad OrderManagerem.

Zamienia zatwierdzone zamiary (RISK_APPROVED) na otwarcie/zamknięcie pary
delta-neutral, delegując orkiestrację (retry, idempotencja, invariant
„delta-neutral albo flat", kompensacja niepełnej pary) do OrderManagera. Sama
emituje zdarzenia pozycji/PnL; zdarzenia zleceń/filli emituje OrderManager.
Obsługuje FLATTEN / EMERGENCY_STOP (domknięcie wszystkiego).
"""
from __future__ import annotations

import logging

from ..adapters.exchange.base import ExchangeAdapter
from ..core.bus import EventBus
from ..core.clock import Clock, RealClock
from ..core.events import Event, EventType, Severity
from ..core.types import MarketTick, TradeIntent
from .book import PositionBook
from .order_manager import OrderManager, PairState

log = logging.getLogger("onewish.execution")


class ExecutionEngine:
    SOURCE = "execution_engine"

    def __init__(self, broker: ExchangeAdapter, book: PositionBook,
                 bus: EventBus | None = None, clock: Clock | None = None) -> None:
        self.broker = broker
        self.book = book
        self._bus = bus
        self._clock = clock or RealClock()
        self._ticks: dict = {}
        self._marks: dict = {}
        self._om = OrderManager(broker, book, clock=self._clock, bus=bus)

    # -- wiring -------------------------------------------------------------- #
    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        self._om._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)
        bus.subscribe(EventType.RISK_APPROVED, self._on_approved)
        bus.subscribe(EventType.FLATTEN, self._on_flatten)
        bus.subscribe(EventType.EMERGENCY_STOP, self._on_flatten)

    async def _on_tick(self, event: Event) -> None:
        t = event.payload
        if isinstance(t, MarketTick):
            self._ticks[t.asset] = t
            self._marks[t.asset] = (t.spot, t.perp)

    async def _on_approved(self, event: Event) -> None:
        payload = event.payload or {}
        intent = payload.get("intent")
        decision = payload.get("decision")
        if not isinstance(intent, TradeIntent) or decision is None or not decision.approved:
            return
        if intent.action == "OPEN":
            await self._open(intent)
        else:
            await self._close(intent.asset)

    # -- otwarcie / zamknięcie (delegacja do OrderManagera) ----------------- #
    async def _open(self, intent: TradeIntent) -> None:
        tick = self._ticks.get(intent.asset)
        if tick is None:
            return
        ts = self._clock.now()
        qty_spot = intent.notional_usd / tick.spot
        qty_perp = intent.notional_usd / tick.perp
        was_open = self.book.is_open(intent.asset)

        pair = await self._om.open_pair(intent.asset, qty_spot, qty_perp, tick.spot, tick.perp)

        if pair.state == PairState.OPEN:
            if not was_open and self._bus:
                await self._bus.publish(Event(EventType.POSITION_OPENED, ts, self.SOURCE,
                                              payload=self.book.position(intent.asset)))
        elif self._bus:
            # OrderManager skompensował niepełną parę do flat (invariant)
            await self._bus.publish(Event(
                EventType.ORDER_REJECTED, ts, self.SOURCE, Severity.WARNING,
                payload={"asset": intent.asset.value,
                         "reason": "niepełna para — kompensacja (flatten)"}))
        await self._emit_pnl(ts)

    @property
    def marks(self) -> dict:
        """Ostatnie znane ceny (spot, perp) per aktywo — do mark-to-market raportów."""
        return dict(self._marks)

    async def _close(self, asset) -> None:
        if not self.book.is_open(asset):
            return
        ts = self._clock.now()
        pos = self.book.position(asset)
        # zamknięcie po CENIE RYNKOWEJ z ostatniego ticka — nie po cenie wejścia
        tick = self._ticks.get(asset)
        await self._om.close_pair(asset,
                                  spot_px=tick.spot if tick is not None else None,
                                  perp_px=tick.perp if tick is not None else None)
        if not self.book.is_open(asset) and self._bus:
            await self._bus.publish(Event(EventType.POSITION_CLOSED, ts, self.SOURCE, payload=pos))
        await self._emit_pnl(ts)

    async def _emit_pnl(self, ts: float) -> None:
        if self._bus:
            await self._bus.publish(Event(EventType.PNL_UPDATE, ts, self.SOURCE,
                                          payload=self.book.snapshot(self._marks, ts)))

    async def _on_flatten(self, event: Event) -> None:
        for asset, pos in list(self.book.positions.items()):
            if pos.is_open:
                await self._close(asset)

    # -- reconcyliacja po restarcie ----------------------------------------- #
    def reconcile(self) -> dict:
        rec = self._om.reconcile()
        rec["open_orders"] = rec.get("pending_orders", [])
        return rec
