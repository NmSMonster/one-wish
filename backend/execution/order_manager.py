"""OrderManager — maszyna stanów cyklu życia zleceń pary delta-neutral (P0).

Najgroźniejszy tryb porażki carry to ORPHAN LEG: jedna noga się wypełnia, druga
nie — i zostajemy z ekspozycją kierunkową. OrderManager pilnuje twardego
INVARIANTU: po każdej próbie otwarcia pozycja jest albo zbilansowana (delta ≈ 0),
albo wyzerowana (flat). Nigdy orphan.

Mechanizmy:
- unikalny client_order_id na każdą próbę (brak duplikatów na giełdzie),
- retry przy odrzuceniu i dosyłka reszty przy partial-fill (do max_attempts),
- przy niepełnej parze → kompensacja (flatten) i stan ABORTED,
- reconcile(): raport stanu (otwarte pozycje, zlecenia w toku, niezbilansowane).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from ..core.bus import EventBus
from ..core.clock import Clock, RealClock
from ..core.events import Event, EventType, Severity
from ..core.types import (
    Asset,
    Leg,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from .book import PositionBook

log = logging.getLogger("onewish.order_manager")


class PairState(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"          # obie nogi zbilansowane
    ABORTED = "ABORTED"    # niepełna para → skompensowana do flat
    CLOSED = "CLOSED"


@dataclass
class ManagedOrder:
    asset: Asset
    leg: Leg
    side: Side
    target_qty: float
    price: float
    filled_qty: float = 0.0
    avg_price: float = 0.0
    state: OrderStatus = OrderStatus.NEW
    attempts: int = 0
    coids: list = field(default_factory=list)


@dataclass
class PairOrder:
    id: str
    asset: Asset
    action: str                      # "OPEN" | "CLOSE"
    spot: ManagedOrder | None = None
    perp: ManagedOrder | None = None
    state: PairState = PairState.PENDING


class OrderManager:
    SOURCE = "order_manager"

    def __init__(self, broker, book: PositionBook, *, max_attempts: int = 3,
                 tol_frac: float = 0.05, clock: Clock | None = None,
                 bus: EventBus | None = None, quantizer=None) -> None:
        self.broker = broker
        self.book = book
        self.max_attempts = max_attempts
        self.tol_frac = tol_frac
        self._clock = clock or RealClock()
        self._bus = bus
        self.quantizer = quantizer    # FilterSet (opt-in): kwantyzuje qty/price do siatki giełdy
        self._seq = 0
        self._pair_seq = 0
        self._orders: dict[str, ManagedOrder] = {}
        self._pairs: list[PairOrder] = []
        self._orphan_alerted: set = set()   # eskalacja raz per aktywo, aż wróci flat/balans

    def _next_coid(self, asset: Asset, leg: Leg) -> str:
        self._seq += 1
        return f"ow-{asset.value}-{leg.value}-{self._seq}"

    async def _emit(self, etype: EventType, payload, ts: float,
                    severity: Severity = Severity.INFO) -> None:
        if self._bus is not None:
            await self._bus.publish(Event(etype, ts, self.SOURCE, severity, payload=payload))

    async def _submit(self, asset: Asset, leg: Leg, side: Side, qty: float,
                      price: float, action: str) -> ManagedOrder:
        """Wypełnia docelową ilość nogi: retry przy odrzuceniu, dosyłka reszty przy
        partial. Każda próba = świeży coid (brak duplikatów na giełdzie)."""
        if self.quantizer is not None:
            qty, price = self.quantizer.quantize(asset, leg, qty, price)
        mo = ManagedOrder(asset=asset, leg=leg, side=side, target_qty=qty, price=price)
        if qty <= 0:                  # poniżej step size — nie da się złożyć
            mo.state = OrderStatus.REJECTED
            return mo
        # Filtry symbolu (minQty/minNotional): nie wysyłaj zlecenia, które giełda i tak
        # odrzuci — marnuje próby i grozi orphanem (jedna noga poniżej minimum). Lepiej
        # od razu REJECTED → OrderManager skompensuje parę do flat (invariant).
        if self.quantizer is not None:
            f = self.quantizer.get(asset, leg)
            if f is not None and not f.ok(price, qty):
                await self._emit(EventType.ORDER_REJECTED,
                                 {"coid": None, "asset": asset.value, "leg": leg.value,
                                  "reason": f"poniżej minimum symbolu (minQty/minNotional): "
                                            f"qty={qty}, notional={price * qty:.2f}$"},
                                 self._clock.now(), Severity.WARNING)
                mo.state = OrderStatus.REJECTED
                return mo
        while mo.attempts < self.max_attempts and mo.filled_qty < qty - 1e-9:
            mo.attempts += 1
            coid = self._next_coid(asset, leg)
            mo.coids.append(coid)
            self._orders[coid] = mo
            remaining = qty - mo.filled_qty
            ts = self._clock.now()
            req = OrderRequest(coid, asset, leg, side, OrderType.MARKET, price, remaining, ts, action)
            await self._emit(EventType.ORDER_REQUEST, req, ts)
            result = await self.broker.submit(req)

            if result.uncertain:
                # Zgubiony ack: zlecenie MOGŁO dojść do giełdy. Ponowienie z nowym coid
                # groziłoby PODWÓJNYM fillem → najpierw uzgodnij stan TEGO coid.
                if not await self._reconcile_after_uncertain(mo, asset, leg, coid, ts):
                    break              # stanu nie ustalono → przerwij (zero ryzyka dubla)
                continue               # zastosowano ewentualne fille; pętla dośle resztę

            if result.status == OrderStatus.REJECTED:
                await self._emit(EventType.ORDER_REJECTED, {"coid": coid, "reason": result.reason},
                                 ts, Severity.WARNING)
                continue
            for fill in result.fills:
                self.book.apply_fill(fill, fill.ts)
                self._apply_fill_to_order(mo, fill)
                et = (EventType.PARTIAL_FILL
                      if result.status == OrderStatus.PARTIALLY_FILLED else EventType.FILL)
                await self._emit(et, fill, fill.ts)

        if mo.filled_qty >= qty - 1e-9:
            mo.state = OrderStatus.FILLED
        elif mo.filled_qty > 0:
            mo.state = OrderStatus.PARTIALLY_FILLED
        else:
            mo.state = OrderStatus.REJECTED
        return mo

    async def _reconcile_after_uncertain(self, mo: ManagedOrder, asset: Asset, leg: Leg,
                                         coid: str, ts: float) -> bool:
        """Po zgubionym acku pyta giełdę o stan `coid`. Zwraca:
        - True  → stan ustalony: zastosowano ewentualne fille; dosłanie reszty bezpieczne,
        - False → stanu NIE da się ustalić: przerwij, NIE ponawiaj (ryzyko podwójnego filla).

        `query_order` zwraca None (zlecenie niezłożone → bezpieczne ponowienie) albo
        OrderResult (zlecenie istnieje → liczymy jego fille). Każdy błąd zapytania
        traktujemy jako stan nieustalony (fail-safe)."""
        try:
            q = await self.broker.query_order(asset, leg, coid)
        except Exception as exc:  # noqa: BLE001 — nieustalony stan MUSI zatrzymać retry
            await self._emit(EventType.ORDER_REJECTED,
                             {"coid": coid, "reason": f"lost-ack: nieustalony stan ({exc}) — przerwano"},
                             ts, Severity.CRITICAL)
            return False
        if q is None:
            await self._emit(EventType.ORDER_REJECTED,
                             {"coid": coid, "reason": "lost-ack: zlecenie niezłożone — ponawiam"},
                             ts, Severity.WARNING)
            return True
        for fill in q.fills:
            self.book.apply_fill(fill, fill.ts)
            self._apply_fill_to_order(mo, fill)
            await self._emit(EventType.FILL, fill, fill.ts)
        return True

    @staticmethod
    def _apply_fill_to_order(mo: ManagedOrder, fill) -> None:
        """avg_price = średnia WAŻONA ilością (nie cena ostatniego filla — przy
        dosyłce reszty po partial-fill ostatnia cena przekłamywała średnią)."""
        prev_qty = mo.filled_qty
        mo.filled_qty = prev_qty + fill.qty
        if mo.filled_qty > 0:
            mo.avg_price = (mo.avg_price * prev_qty + fill.price * fill.qty) / mo.filled_qty

    def _balanced(self, asset: Asset, qty_ref: float) -> bool:
        pos = self.book.position(asset)
        if pos is None or not pos.is_open:
            return False
        if abs(pos.spot_qty) < 1e-12 or abs(pos.perp_qty) < 1e-12:
            return False
        return abs(pos.net_delta) <= self.tol_frac * max(1e-12, qty_ref)

    async def _escalate_if_orphan(self, asset: Asset, qty_ref: float, ts: float) -> None:
        """Inwariant w wersji uczciwej: „zbilansowana albo flat, a jeśli świat
        odmówi — GŁOŚNO". Gdy nawet kompensacja nie przechodzi (giełda odrzuca /
        gubi acki także dla zleceń zamykających), pozycja może zostać niezbilansowana.
        Ciche ABORTED to najgorszy możliwy stan (goła ekspozycja bez nadzoru) —
        eskalujemy EMERGENCY_STOP: risk kill, alert CRITICAL do operatora,
        ExecutionEngine ponawia domknięcie. Emisja raz per aktywo (bez pętli
        zdarzeń), kasowana gdy pozycja wróci do flat/balansu."""
        pos = self.book.position(asset)
        if pos is None or not pos.is_open or self._balanced(asset, qty_ref):
            self._orphan_alerted.discard(asset)
            return
        if asset in self._orphan_alerted:
            return
        self._orphan_alerted.add(asset)
        log.critical("ORPHAN LEG %s po nieudanej kompensacji: spot=%.10g perp=%.10g",
                     asset.value, pos.spot_qty, pos.perp_qty)
        await self._emit(
            EventType.EMERGENCY_STOP,
            {"reason": f"orphan leg {asset.value}: kompensacja nieudana "
                       f"(spot={pos.spot_qty:.10g}, perp={pos.perp_qty:.10g}) — "
                       "wymagana interwencja"},
            ts, Severity.CRITICAL)

    async def _flatten(self, asset: Asset, spot_px: float | None = None,
                       perp_px: float | None = None) -> None:
        """Domyka pozycję po CENIE RYNKOWEJ (spot_px/perp_px), nie po cenie wejścia.
        Zamknięcie po entry fałszowałoby PnL: paper broker fill'uje po req.price,
        więc cały ruch ceny od wejścia znikałby z realized. Fallback do entry tylko
        gdy ceny rynkowej nie znamy (brak ticka — lepsze to niż brak domknięcia)."""
        pos = self.book.position(asset)
        if pos is None or not pos.is_open:
            return
        if pos.spot_qty > 1e-9:
            px = spot_px if spot_px and spot_px > 0 else pos.spot_entry
            await self._submit(asset, Leg.SPOT, Side.SELL, pos.spot_qty, px, "CLOSE")
        pos = self.book.position(asset)
        if pos is not None and pos.perp_qty < -1e-9:
            px = perp_px if perp_px and perp_px > 0 else pos.perp_entry
            await self._submit(asset, Leg.PERP, Side.BUY, abs(pos.perp_qty), px, "CLOSE")

    async def open_pair(self, asset: Asset, qty_spot: float, qty_perp: float,
                        spot_px: float, perp_px: float) -> PairOrder:
        self._pair_seq += 1
        spot = await self._submit(asset, Leg.SPOT, Side.BUY, qty_spot, spot_px, "OPEN")
        perp = await self._submit(asset, Leg.PERP, Side.SELL, qty_perp, perp_px, "OPEN")
        pair = PairOrder(id=f"pair-{self._pair_seq}", asset=asset, action="OPEN",
                         spot=spot, perp=perp)

        if self._balanced(asset, qty_spot):
            pair.state = PairState.OPEN
            self._orphan_alerted.discard(asset)
        else:
            # INVARIANT: nie zostawiamy orphan leg — kompensujemy do flat
            log.warning("Niepełna para %s — kompensacja (flatten)", asset.value)
            await self._flatten(asset, spot_px, perp_px)
            pair.state = PairState.ABORTED
            # kompensacja też mogła nie przejść (chaos totalny) → głośna eskalacja
            await self._escalate_if_orphan(asset, qty_spot, self._clock.now())
        self._pairs.append(pair)
        return pair

    async def close_pair(self, asset: Asset, spot_px: float | None = None,
                         perp_px: float | None = None) -> PairOrder:
        """Zamyka parę po CENIE RYNKOWEJ (jak _flatten) — entry tylko jako fallback,
        gdy wołający nie zna bieżących cen (np. testy jednostkowe bez ticków)."""
        self._pair_seq += 1
        pair = PairOrder(id=f"pair-{self._pair_seq}", asset=asset, action="CLOSE")
        pos = self.book.position(asset)
        if pos is None or not pos.is_open:
            pair.state = PairState.CLOSED
            return pair
        if pos.spot_qty > 1e-9:
            px = spot_px if spot_px and spot_px > 0 else pos.spot_entry
            pair.spot = await self._submit(asset, Leg.SPOT, Side.SELL, pos.spot_qty,
                                           px, "CLOSE")
        pos = self.book.position(asset)
        if pos is not None and pos.perp_qty < -1e-9:
            px = perp_px if perp_px and perp_px > 0 else pos.perp_entry
            pair.perp = await self._submit(asset, Leg.PERP, Side.BUY, abs(pos.perp_qty),
                                           px, "CLOSE")
        pair.state = PairState.CLOSED if not self.book.is_open(asset) else PairState.OPEN
        if pair.state == PairState.CLOSED:
            self._orphan_alerted.discard(asset)
        else:
            # zamknięcie jednej nogi przeszło, drugiej nie → orphan; nie milczymy
            qty_ref = abs(pos.spot_qty) if pos is not None else 1.0
            await self._escalate_if_orphan(asset, max(qty_ref, 1e-9), self._clock.now())
        self._pairs.append(pair)
        return pair

    def reconcile(self, venue_open_orders: list | None = None) -> dict:
        """Raport stanu do uzgodnienia po restarcie. W live: porównaj z venue_open_orders."""
        pending = [coid for coid, o in self._orders.items()
                   if o.state not in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED)]
        open_positions = [a.value for a, p in self.book.positions.items() if p.is_open]
        imbalanced = []
        for asset, p in self.book.positions.items():
            if p.is_open and (abs(p.spot_qty) < 1e-9 or abs(p.perp_qty) < 1e-9
                              or abs(p.net_delta) > 0.1 * max(1e-9, abs(p.spot_qty))):
                imbalanced.append(asset.value)
        return {"open_positions": open_positions, "pending_orders": pending,
                "imbalanced": imbalanced}
