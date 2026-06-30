"""FairValueModel (M4) — uczciwa relacja perp↔spot.

Dla strategii cash-and-carry uczciwy basis perpa względem indeksu powinien
odpowiadać oczekiwanemu carry funding do najbliższego rozliczenia (plus drobny
koszt nośności). Jeśli obserwowany basis jest wyżej niż uczciwy, para
long-spot/short-perp łapie konwergencję + zbiera funding.

Model nie ma "zarabiać" — odpowiada na pytanie: jaki basis jest teraz uczciwy?
"""
from __future__ import annotations

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import FairValue, MarketTick


class FairValueModel:
    SOURCE = "fair_value_model"

    def __init__(
        self,
        *,
        carry_cost_bps: float = 0.0,
        funding_period_s: float = 8 * 3600.0,
        time_scale: bool = False,
        lag_ref_ms: float = 2000.0,
        spread_ref_bps: float = 10.0,
    ) -> None:
        self.carry_cost_bps = carry_cost_bps
        self.funding_period_s = funding_period_s
        self.time_scale = time_scale
        self.lag_ref_ms = lag_ref_ms
        self.spread_ref_bps = spread_ref_bps
        self._bus: EventBus | None = None

    def evaluate(self, tick: MarketTick) -> FairValue:
        funding_bps = tick.predicted_funding * 1e4

        if self.time_scale and tick.seconds_to_funding > 0:
            frac = min(1.0, tick.seconds_to_funding / self.funding_period_s)
            expected_funding_bps = funding_bps * frac
        else:
            expected_funding_bps = funding_bps

        fair_basis_bps = expected_funding_bps + self.carry_cost_bps

        # zaufanie spada przy nieświeżych danych i szerokim spreadzie
        avg_spread = (tick.spot_spread_bps + tick.perp_spread_bps) / 2.0
        confidence = 1.0
        confidence -= min(1.0, tick.data_lag_ms / self.lag_ref_ms)
        confidence -= min(1.0, avg_spread / self.spread_ref_bps)
        confidence = max(0.0, min(1.0, confidence))

        reason = (
            f"fair_basis = funding {funding_bps:+.2f}bps "
            f"+ carry {self.carry_cost_bps:+.2f}bps"
        )
        return FairValue(
            asset=tick.asset,
            ts=tick.ts,
            fair_basis_bps=fair_basis_bps,
            expected_funding_bps=expected_funding_bps,
            carry_cost_bps=self.carry_cost_bps,
            confidence=confidence,
            reason=reason,
        )

    # -- wiring na szynę (publikacja FAIR_VALUE dla audytu/GUI) -------------- #
    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick) or self._bus is None:
            return
        fv = self.evaluate(tick)
        await self._bus.publish(Event(EventType.FAIR_VALUE, fv.ts, self.SOURCE, payload=fv))
