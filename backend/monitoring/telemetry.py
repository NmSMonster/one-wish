"""CostTelemetry — pomiar REALNYCH kosztów egzekucji i rynku (shadow vs model).

Edge carry jest cienki, a głównym jego zabójcą są koszty (prowizje + poślizg +
spread). Model (`CostModel`) ZAKŁADA te koszty; telemetria MIERZY je z realnych
fillów i ticków, żeby werdykt o przewadze był uczciwy, a nie oparty na optymistycznych
założeniach. To „shadow": liczymy bez wpływu na decyzje, tylko obserwujemy.

Mierzone:
- poślizg egzekucji per noga = (cena fill vs cena referencyjna ze zlecenia), bps,
  zawsze jako koszt dodatni (BUY drożej / SELL taniej od referencji),
- realna prowizja per noga = fee / nominał, bps,
- realny spread rynku (spot/perp) i opóźnienie danych — z MARKET_TICK.

Parowanie fill→referencja po client_order_id (każda próba zlecenia ma swój coid).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import Fill, Leg, MarketTick, OrderRequest, Side


@dataclass
class Running:
    """Lekki akumulator statystyk strumieniowych (bez trzymania próbek)."""
    n: int = 0
    sum: float = 0.0
    min: float = float("inf")
    max: float = float("-inf")

    def add(self, x: float) -> None:
        self.n += 1
        self.sum += x
        if x < self.min:
            self.min = x
        if x > self.max:
            self.max = x

    @property
    def mean(self) -> float:
        return self.sum / self.n if self.n else 0.0

    def snapshot(self) -> dict:
        return {
            "n": self.n,
            "mean": self.mean,
            "min": self.min if self.n else 0.0,
            "max": self.max if self.n else 0.0,
        }


class CostTelemetry:
    SOURCE = "cost_telemetry"

    def __init__(self, *, ref_maxlen: int = 10_000) -> None:
        self._ref: dict[str, float] = {}          # coid -> cena referencyjna ze zlecenia
        self._ref_maxlen = ref_maxlen
        self.slippage_bps: dict[Leg, Running] = {Leg.SPOT: Running(), Leg.PERP: Running()}
        self.fee_bps: dict[Leg, Running] = {Leg.SPOT: Running(), Leg.PERP: Running()}
        self.spread_bps_spot = Running()
        self.spread_bps_perp = Running()
        self.data_lag_ms = Running()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(EventType.ORDER_REQUEST, self._on_req)
        bus.subscribe(EventType.FILL, self._on_fill)
        bus.subscribe(EventType.PARTIAL_FILL, self._on_fill)
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    def _remember(self, coid: str, price: float) -> None:
        self._ref[coid] = price
        if len(self._ref) > self._ref_maxlen:        # ogranicz pamięć: usuń najstarszy
            self._ref.pop(next(iter(self._ref)), None)

    async def _on_req(self, event: Event) -> None:
        req = event.payload
        if isinstance(req, OrderRequest) and req.price > 0:
            self._remember(req.client_order_id, req.price)

    async def _on_fill(self, event: Event) -> None:
        fill = event.payload
        if not isinstance(fill, Fill):
            return
        ref = self._ref.get(fill.client_order_id)    # get (nie pop): kilka fillów na jeden coid
        if ref and ref > 0 and fill.price > 0:
            if fill.side == Side.BUY:
                slip = (fill.price / ref - 1.0) * 1e4     # kupiliśmy drożej = koszt dodatni
            else:
                slip = (1.0 - fill.price / ref) * 1e4     # sprzedaliśmy taniej = koszt dodatni
            self.slippage_bps[fill.leg].add(slip)
        notional = fill.price * fill.qty
        if notional > 0:
            self.fee_bps[fill.leg].add(fill.fee / notional * 1e4)

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick):
            return
        self.spread_bps_spot.add(tick.spot_spread_bps)
        self.spread_bps_perp.add(tick.perp_spread_bps)
        self.data_lag_ms.add(tick.data_lag_ms)

    def snapshot(self) -> dict:
        return {
            "slippage_bps": {leg.value: r.snapshot() for leg, r in self.slippage_bps.items()},
            "fee_bps": {leg.value: r.snapshot() for leg, r in self.fee_bps.items()},
            "spread_bps": {"spot": self.spread_bps_spot.snapshot(),
                           "perp": self.spread_bps_perp.snapshot()},
            "data_lag_ms": self.data_lag_ms.snapshot(),
            "pending_refs": len(self._ref),
        }
