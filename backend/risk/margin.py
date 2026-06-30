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

    def _mmr(self, mmr: float | None) -> float:
        """Maintenance margin rate do użycia — opcjonalne nadpisanie per symbol
        (bracket), inaczej domyślny rate modelu."""
        return self.maintenance_margin_rate if mmr is None else mmr

    def required_margin(self, notional: float, survive_move_frac: float,
                        *, mmr: float | None = None) -> float:
        """Depozyt na nogę short, by przeżyć ruch perpa w górę o `survive_move_frac`."""
        return notional * (survive_move_frac + self._mmr(mmr))

    def max_safe_leverage(self, survive_move_frac: float, *, mmr: float | None = None) -> float:
        """Maks. dźwignia, przy której short przeżyje ruch o `survive_move_frac`."""
        return 1.0 / (survive_move_frac + self._mmr(mmr))

    def liquidation_price_short(self, entry: float, leverage: float,
                                *, mmr: float | None = None) -> float:
        """Cena likwidacji shorta (isolated): rośnie w górę od entry."""
        return entry * (1.0 + 1.0 / leverage - self._mmr(mmr))

    def health_short(self, entry: float, mark: float, notional: float,
                     posted_margin: float, *, mmr: float | None = None) -> float:
        """equity / maintenance. ≤ 1 = likwidacja. Im wyżej, tym bezpieczniej."""
        if entry <= 0 or mark <= 0:
            return math.inf
        loss = notional * (mark - entry) / entry          # >0 gdy mark>entry (short traci)
        equity = posted_margin - loss
        maintenance = notional * (mark / entry) * self._mmr(mmr)
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


# Przybliżone maintenance margin rate per aktywo (bracket dla małego nominału carry).
# BTC/ETH najniżej, mniej płynne alty wyżej. To ostrożne wartości — realne brackety
# Binance rosną z nominałem; przy naszych rozmiarach (~$50–kilkaset) jesteśmy w
# najniższym tierze, więc stałe per-symbol są wystarczająco wierne i konserwatywne.
DEFAULT_MAINTENANCE_BY_ASSET: dict = {}   # uzupełniane niżej z mapą stringów


def _build_default_maintenance() -> dict:
    from ..core.types import Asset as _Asset
    table = {"BTC": 0.004, "ETH": 0.005, "SOL": 0.010, "XRP": 0.010}
    out: dict = {}
    for name, rate in table.items():
        try:
            out[_Asset(name)] = rate
        except ValueError:
            continue                       # aktywo spoza enuma — pomiń
    return out


DEFAULT_MAINTENANCE_BY_ASSET = _build_default_maintenance()


class MarginStressTester:
    """Stress-test marginu nogi short-perp: co stanie się ze zdrowiem przy
    niekorzystnym ruchu perpa w górę o +10/20/30% (short traci na wzroście).

    To narzędzie decyzyjne PRZED wejściem/podczas trzymania: czy obecna dźwignia
    przeżyje szok, zanim watchdog zdąży kontrolowanie domknąć. Czysta funkcja stanu
    (book + marki) — bez sieci, deterministyczna. Konfiguracja luster watchdoga
    (perp_leverage, flatten_health), żeby werdykt był spójny z realnym zachowaniem.
    """

    def __init__(self, book: PositionBook, model: MarginModel | None = None, *,
                 perp_leverage: float = 3.0, flatten_health: float = 1.3,
                 shocks: tuple[float, ...] = (0.10, 0.20, 0.30),
                 maintenance_by_asset: dict | None = None) -> None:
        self.book = book
        self.model = model or MarginModel()
        self.perp_leverage = perp_leverage
        self.flatten_health = flatten_health
        self.shocks = tuple(shocks)
        self.maintenance_by_asset = (DEFAULT_MAINTENANCE_BY_ASSET
                                     if maintenance_by_asset is None else maintenance_by_asset)

    def stress(self, marks: dict | None = None) -> dict:
        """Zwraca raport: per pozycja zdrowie pod każdym szokiem + zbiorcze
        podsumowanie (najgorsze zdrowie, aktywa które by się zlikwidowały/domknęły
        przy danym szoku). `marks` = bieżące marki per aktywo (domyślnie entry)."""
        marks = marks or {}
        positions: dict = {}
        min_health = math.inf
        flatten_at: dict = {s: [] for s in self.shocks}
        liquidation_at: dict = {s: [] for s in self.shocks}

        for asset, pos in self.book.positions.items():
            if not pos.is_open or pos.perp_qty >= -1e-12:
                continue                   # tylko otwarte nogi short perp
            entry = pos.perp_entry
            cur_mark = marks.get(asset, entry)
            notional = abs(pos.perp_qty) * entry
            posted = notional / self.perp_leverage if self.perp_leverage > 0 else 0.0
            mmr = self.maintenance_by_asset.get(asset)   # None → domyślny rate modelu

            rows = []
            for s in self.shocks:
                shocked = cur_mark * (1.0 + s)
                health = self.model.health_short(entry, shocked, notional, posted, mmr=mmr)
                liquidated = health <= 1.0
                would_flatten = health <= self.flatten_health
                rows.append({"shock": s, "mark": shocked, "health": health,
                             "liquidated": liquidated, "would_flatten": would_flatten})
                min_health = min(min_health, health)
                if liquidated:
                    liquidation_at[s].append(asset.value)
                if would_flatten:
                    flatten_at[s].append(asset.value)

            positions[asset.value] = {
                "entry": entry, "current_mark": cur_mark, "notional": notional,
                "posted_margin": posted, "maintenance_rate": self.model._mmr(mmr),
                "shocks": rows,
            }

        return {
            "positions": positions,
            "summary": {
                "min_health": min_health,
                "flatten_at": flatten_at,
                "liquidation_at": liquidation_at,
                "safe": min_health > self.flatten_health,
            },
        }
