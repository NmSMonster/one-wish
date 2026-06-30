"""CircuitBreaker — twarde blokady reżimu rynkowego.

Niezależnie od sygnału i limitów, są stany rynku, w których po prostu NIE wchodzimy:
- anomalia premii (mark daleko od indeksu) → rynek dislocated/manipulowany,
- gwałtowny skok open interest → rozpędzone lewarowanie / ryzyko unwindu,
- wysychająca głębokość → poślizg i pułapka płynności,
- kaskada likwidacji → perp odrywa się od indeksu, niestabilnie.

Gdy warunek trafiony, aktywo ma „otwarty bezpiecznik" — RiskManager odrzuca nowe
wejścia, dopóki reżim nie wróci do normy. Bezpieczne dla syntetyku (premia/OI = 0).
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque

from ..core.bus import EventBus
from ..core.events import Event, EventType, Severity
from ..core.types import Asset, MarketTick

log = logging.getLogger("onewish.circuit")


class CircuitBreaker:
    SOURCE = "circuit_breaker"

    def __init__(self, *, max_abs_premium_bps: float = 40.0, oi_jump_frac: float = 0.30,
                 oi_window: int = 30, min_depth_usd: float = 20_000.0,
                 liq_window_s: float = 60.0, max_liq_notional_usd: float = 5_000_000.0,
                 bus: EventBus | None = None) -> None:
        self.max_abs_premium_bps = max_abs_premium_bps
        self.oi_jump_frac = oi_jump_frac
        self.min_depth_usd = min_depth_usd
        self.liq_window_s = liq_window_s
        self.max_liq_notional_usd = max_liq_notional_usd
        self._bus = bus
        self._open: set = set()
        self._oi: dict = defaultdict(lambda: deque(maxlen=oi_window))
        self._liq: dict = defaultdict(list)

    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    def is_open(self, asset: Asset) -> bool:
        return asset in self._open

    def note_liquidation(self, asset: Asset, notional_usd: float, ts: float) -> None:
        self._liq[asset].append((ts, notional_usd))

    def _liq_notional(self, asset: Asset, now: float) -> float:
        recent = [(t, n) for t, n in self._liq[asset] if now - t <= self.liq_window_s]
        self._liq[asset] = recent
        return sum(n for _, n in recent)

    def _reasons(self, tick: MarketTick) -> list[str]:
        reasons: list[str] = []
        if abs(tick.premium_bps) > self.max_abs_premium_bps:
            reasons.append(f"premia {tick.premium_bps:+.1f}bps")
        if min(tick.spot_depth_usd, tick.perp_depth_usd) < self.min_depth_usd:
            reasons.append("wysychająca głębokość")

        hist = self._oi[tick.asset]
        if tick.open_interest_usd > 0:
            if hist and hist[0] > 0:
                jump = abs(tick.open_interest_usd - hist[0]) / hist[0]
                if jump > self.oi_jump_frac:
                    reasons.append(f"skok OI {jump * 100:.0f}%")
            hist.append(tick.open_interest_usd)

        if self._liq_notional(tick.asset, tick.ts) > self.max_liq_notional_usd:
            reasons.append("kaskada likwidacji")
        return reasons

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick):
            return
        reasons = self._reasons(tick)
        if reasons:
            if tick.asset not in self._open:
                self._open.add(tick.asset)
                if self._bus is not None:
                    await self._bus.publish(Event(
                        EventType.RISK_LIMIT_BREACH, tick.ts, self.SOURCE, Severity.CRITICAL,
                        payload={"asset": tick.asset.value, "action": "circuit_open",
                                 "reasons": reasons}))
        elif tick.asset in self._open:
            self._open.discard(tick.asset)
            if self._bus is not None:
                await self._bus.publish(Event(
                    EventType.RISK_LIMIT_BREACH, tick.ts, self.SOURCE, Severity.INFO,
                    payload={"asset": tick.asset.value, "action": "circuit_close"}))
