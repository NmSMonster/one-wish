"""Monitor (M11/M12) — watchdog bezpieczeństwa.

Niezależnie od Risk Managera pilnuje twardych warunków awaryjnych i emituje
EMERGENCY_STOP (który kill-uje Risk Managera i każe Execution domknąć pozycje):
- przekroczony DZIENNY limit straty NETTO (realized + unrealized + funding − fees,
  liczone jako delta od początku doby UTC). Netto, bo dla delta-neutral carry
  realized ≈ 0 przez cały czas trzymania — realny drawdown (rozjazd basis) siedzi
  w unrealized i guard patrzący tylko na realized NIGDY by go nie zauważył.
  Dzienny naprawdę: baza resetuje się na przełomie doby (ts eventu, UTC), więc
  wielodniowa sesja nie kumuluje wczorajszej straty do dzisiejszego limitu,
- zamrożony feed danych — DWIE ścieżki: STALE_FEED z adaptera (luka między
  tickami, wykrywana przy NASTĘPNYM ticku) oraz heartbeat (własny task), który
  łapie przypadek, gdy feed po prostu MILKNIE i następny tick nigdy nie przyjdzie
  — wtedy adapter nie ma okazji nic wyemitować i bez heartbeatu bot wisiałby
  z otwartymi pozycjami na martwych danych.
EMERGENCY_STOP emitujemy tylko raz, żeby nie zalać szyny.
"""
from __future__ import annotations

import asyncio
import logging

from ..core.bus import EventBus
from ..core.clock import Clock, RealClock
from ..core.events import Event, EventType, Severity
from ..core.types import MarketTick, PnLSnapshot

log = logging.getLogger("onewish.monitor")


class Monitor:
    SOURCE = "monitoring"

    def __init__(self, bus: EventBus | None = None, *, max_daily_loss_usd: float | None = None,
                 stale_after_s: float = 10.0, clock: Clock | None = None) -> None:
        self._bus = bus
        self.max_daily_loss_usd = max_daily_loss_usd
        self.stale_after_s = stale_after_s
        self._clock = clock or RealClock()
        self._last_tick_wall: float | None = None
        self._stopped = False
        self._hb_task: asyncio.Task | None = None
        # dzienna baza NETTO: strata liczona jako delta od początku doby (UTC),
        # nie skumulowana od startu procesu
        self._day: int | None = None
        self._baseline_net = 0.0
        self._last_net = 0.0

    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)
        bus.subscribe(EventType.PNL_UPDATE, self._on_pnl)
        bus.subscribe(EventType.STALE_FEED, self._on_stale)

    async def _on_tick(self, event: Event) -> None:
        if isinstance(event.payload, MarketTick):
            self._last_tick_wall = self._clock.now()

    async def _on_stale(self, event: Event) -> None:
        # zamrożony feed = nie ufamy rynkowi → twardy stop (flatten + kill)
        if self._bus is None or self._stopped:
            return
        gap = ""
        if isinstance(event.payload, dict) and "gap_s" in event.payload:
            gap = f" (luka {event.payload['gap_s']:.0f}s)"
        await self._emergency(event.ts, f"zamrożony feed danych{gap}")

    async def _on_pnl(self, event: Event) -> None:
        snap = event.payload
        if not isinstance(snap, PnLSnapshot) or self._bus is None or self._stopped:
            return
        # przełom doby (UTC, po ts eventu — deterministyczne w testach/backteście):
        # baza = ostatnie NETTO poprzedniej doby; strata dzienna to delta od bazy
        day = int(event.ts // 86_400)
        if self._day is None:
            self._day = day
        elif day != self._day:
            self._day = day
            self._baseline_net = self._last_net
        self._last_net = snap.net

        if self.max_daily_loss_usd is None:
            return
        day_net = snap.net - self._baseline_net
        if day_net <= -self.max_daily_loss_usd:
            await self._emergency(
                event.ts,
                f"dzienny limit straty NETTO {day_net:.2f}$ "
                f"(realized {snap.realized:+.2f}, unrealized {snap.unrealized:+.2f}, "
                f"funding {snap.funding_collected:+.2f}, fees {snap.fees_paid:.2f})")

    async def _emergency(self, ts: float, reason: str) -> None:
        self._stopped = True
        log.critical("EMERGENCY_STOP: %s", reason)
        await self._bus.publish(Event(EventType.EMERGENCY_STOP, ts, self.SOURCE,
                                      Severity.CRITICAL, payload={"reason": reason}))

    def is_stale(self, now: float | None = None) -> bool:
        now = now if now is not None else self._clock.now()
        return self._last_tick_wall is not None and (now - self._last_tick_wall) > self.stale_after_s

    # -- heartbeat: aktywna detekcja MILCZĄCEGO feedu ------------------------ #
    def start_heartbeat(self, interval_s: float = 1.0) -> asyncio.Task:
        """Uruchamia task cyklicznie sprawdzający świeżość feedu. Konieczny w live:
        STALE_FEED z adaptera powstaje dopiero przy następnym ticku — gdy źródło
        zamilknie całkiem, tylko heartbeat wyemituje EMERGENCY_STOP (flatten+kill)."""
        self._hb_task = asyncio.create_task(self._heartbeat(interval_s))
        return self._hb_task

    def stop_heartbeat(self) -> None:
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None

    async def _heartbeat(self, interval_s: float) -> None:
        try:
            while not self._stopped:
                if self._bus is not None and self.is_stale():
                    gap = self._clock.now() - (self._last_tick_wall or 0.0)
                    await self._emergency(self._clock.now(),
                                          f"feed milczy {gap:.0f}s > {self.stale_after_s:.0f}s (heartbeat)")
                    return                      # emergency raz; task kończy pracę
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            pass
