"""Model marginu i watchdog likwidacji nogi short-perp.

Carry jest delta-neutral (neutralny na KIERUNEK ceny), ale noga short perp żyje na
depozycie. Jeśli perp wystrzeli w górę, short traci; gdy strata zje depozyt do
poziomu maintenance — giełda LIKWIDUJE nogę perp po złej cenie, pęka hedge i
strata się krystalizuje. Dlatego:

- trzymamy NISKĄ dźwignię na nodze perp (dużo depozytu) → liquidation price daleko,
- monitorujemy „zdrowie" marginu i KONTROLOWANIE domykamy parę ZANIM dojdzie do
  likwidacji (przy kontrolowanym zamknięciu zysk na long spot pokrywa stratę na
  short perp → blisko zera; przy likwidacji → realna strata).

Definicja zdrowia: health = equity / maintenance_margin (likwidacja przy health ≤ 1).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from ..core.bus import EventBus
from ..core.events import Event, EventType, Severity
from ..core.types import MarketTick, TradeIntent
from ..execution.book import PositionBook

log = logging.getLogger("onewish.margin")


@dataclass
class MarginModel:
    maintenance_margin_rate: float = 0.005   # ~0.5% (BTC; alty zwykle wyżej)

    def required_margin(self, notional: float, survive_move_frac: float) -> float:
        """Depozyt na nogę short, by przeżyć ruch perpa w górę o `survive_move_frac`."""
        return notional * (survive_move_frac + self.maintenance_margin_rate)

    def max_safe_leverage(self, survive_move_frac: float) -> float:
        """Maks. dźwignia, przy której short przeżyje ruch o `survive_move_frac`."""
        return 1.0 / (survive_move_frac + self.maintenance_margin_rate)

    def liquidation_price_short(self, entry: float, leverage: float) -> float:
        """Cena likwidacji shorta (isolated): rośnie w górę od entry."""
        return entry * (1.0 + 1.0 / leverage - self.maintenance_margin_rate)

    def health_short(self, entry: float, mark: float, notional: float,
                     posted_margin: float) -> float:
        """equity / maintenance. ≤ 1 = likwidacja. Im wyżej, tym bezpieczniej."""
        if entry <= 0 or mark <= 0:
            return math.inf
        loss = notional * (mark - entry) / entry          # >0 gdy mark>entry (short traci)
        equity = posted_margin - loss
        maintenance = notional * (mark / entry) * self.maintenance_margin_rate
        if maintenance <= 0:
            return math.inf
        return equity / maintenance


class MarginWatchdog:
    SOURCE = "margin_watchdog"

    def __init__(self, book: PositionBook, model: MarginModel | None = None, *,
                 perp_leverage: float = 3.0, warn_health: float = 1.8,
                 flatten_health: float = 1.3, bus: EventBus | None = None) -> None:
        self.book = book
        self.model = model or MarginModel()
        self.perp_leverage = perp_leverage
        self.warn_health = warn_health
        self.flatten_health = flatten_health
        self._bus = bus
        self._state: dict = {}   # asset -> 'ok' | 'warn' | 'critical'

    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick) or self._bus is None:
            return
        pos = self.book.position(tick.asset)
        if pos is None or not pos.is_open or pos.perp_qty >= -1e-12:
            self._state.pop(tick.asset, None)
            return

        mark = tick.mark_price if tick.mark_price > 0 else tick.perp
        notional = abs(pos.perp_qty) * pos.perp_entry
        posted = notional / self.perp_leverage
        health = self.model.health_short(pos.perp_entry, mark, notional, posted)
        prev = self._state.get(tick.asset, "ok")

        if health <= self.flatten_health:
            if prev != "critical":
                self._state[tick.asset] = "critical"
                await self._bus.publish(Event(
                    EventType.MARGIN_WARNING, tick.ts, self.SOURCE, Severity.CRITICAL,
                    payload={"asset": tick.asset.value, "health": health, "action": "flatten"}))
                # kontrolowane domknięcie pary PRZED likwidacją
                await self._bus.publish(Event(
                    EventType.TRADE_INTENT, tick.ts, self.SOURCE,
                    payload=TradeIntent(tick.asset, tick.ts, "CLOSE", 0.0, 0.0,
                                        f"margin health {health:.2f} ≤ {self.flatten_health} — flatten przed likwidacją")))
        elif health <= self.warn_health:
            if prev == "ok":
                self._state[tick.asset] = "warn"
                await self._bus.publish(Event(
                    EventType.MARGIN_WARNING, tick.ts, self.SOURCE, Severity.WARNING,
                    payload={"asset": tick.asset.value, "health": health, "action": "warn"}))
        else:
            self._state[tick.asset] = "ok"
