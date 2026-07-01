"""RepricingDetector (M5) — sygnał wejścia. Dwa tryby:

- "carry" (DOMYŚLNY, właściwy dla strategii): wchodzi, gdy forward funding jest
  dodatni z marginesem, basis nie jest odwrócony, a rynek płynny. NIE wymaga
  dyslokacji — bo edge carry to dochód z funding inkasowany przez trzymanie, a
  koszt round-trip amortyzuje się w czasie. (Replay realnych danych pokazał, że
  bramkowanie wejścia dyslokacją dawało 0 wejść — bot nie łapał własnego edge.)
- "dislocation": klasyczna „opóźniona wycena" — wejście na perp-rich spike, gdy
  dyslokacja + carry przebijają koszt w jednym strzale. Zostawione do badań.

Próg płynności jest WZGLĘDNY do wielkości pozycji (depth ≥ depth_mult × notional),
bo realny top-of-book bywa cienki, a my gramy małym nominałem.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import MarketTick, Signal, SignalState
from ..model.costs import CostModel
from ..model.fair_value import FairValueModel

log = logging.getLogger("onewish.detector")


class RepricingDetector:
    SOURCE = "repricing_detector"

    def __init__(
        self,
        model: FairValueModel,
        cost_model: CostModel,
        *,
        entry_mode: str = "carry",
        notional_usd: float = 200.0,
        min_funding_bps: float = 0.1,        # min forward funding/8h (~1.1%/rok floor); carry trzyma długo, amortyzuje koszt
        basis_floor_bps: float = 10.0,       # nie wchodzimy, gdy basis < −floor (perp za tani)
        min_edge_bps: float = 2.0,           # tryb dislocation
        min_dislocation_bps: float = 4.0,    # tryb dislocation
        require_positive_funding: bool = True,
        max_spread_bps: float = 8.0,
        depth_mult: float = 5.0,             # depth ≥ depth_mult × notional
        max_data_lag_ms: float = 5000.0,     # tolerujemy skew zegara; realną nieświeżość łapie STALE_FEED
        min_seconds_to_funding: float = 60.0,
        sizer=None,
    ) -> None:
        self.model = model
        self.cost_model = cost_model
        self.entry_mode = entry_mode
        self.notional_usd = notional_usd
        self.min_funding_bps = min_funding_bps
        self.basis_floor_bps = basis_floor_bps
        self.min_edge_bps = min_edge_bps
        self.min_dislocation_bps = min_dislocation_bps
        self.require_positive_funding = require_positive_funding
        self.max_spread_bps = max_spread_bps
        self.depth_mult = depth_mult
        self.max_data_lag_ms = max_data_lag_ms
        self.min_seconds_to_funding = min_seconds_to_funding
        # Tier A: gdy sizer aktywny (tryb carry), depth-check i koszt MUSZĄ liczyć się
        # na tym samym efektywnym nominale, jaki realnie wystawi StrategyPolicy —
        # inaczej ważona (większa) pozycja przechodzi bramkę płynności policzoną dla
        # mniejszego, płaskiego nominału i margines bezpieczeństwa jest zawyżony.
        self.sizer = sizer
        self._last_state: dict = {}
        self._bus: EventBus | None = None

    def _effective_notional(self, funding_bps: float) -> float:
        if self.sizer is not None and self.entry_mode == "carry":
            return self.sizer.size(funding_bps)
        return self.notional_usd

    def _quality_reasons(self, tick: MarketTick, notional_usd: float) -> list[str]:
        reasons: list[str] = []
        if max(tick.spot_spread_bps, tick.perp_spread_bps) > self.max_spread_bps:
            reasons.append("spread za szeroki")
        if min(tick.spot_depth_usd, tick.perp_depth_usd) < self.depth_mult * notional_usd:
            reasons.append("za mała płynność")
        if tick.data_lag_ms > self.max_data_lag_ms:
            reasons.append("dane nieświeże")
        if tick.seconds_to_funding < self.min_seconds_to_funding:
            reasons.append("za blisko rozliczenia")
        return reasons

    def evaluate(self, tick: MarketTick) -> Signal:
        fv = self.model.evaluate(tick)
        observed = tick.basis_bps
        dislocation = observed - fv.fair_basis_bps
        funding_bps = tick.predicted_funding * 1e4
        carry = fv.expected_funding_bps

        # Efektywny nominał: jeśli sizer aktywny, to TO on decyduje o realnej wielkości
        # pozycji — koszt i próg płynności muszą liczyć się na tej samej wartości.
        notional = self._effective_notional(funding_bps)
        cost = self.cost_model.round_trip_cost_bps(tick, notional)
        reasons = self._quality_reasons(tick, notional)

        if self.entry_mode == "dislocation":
            net = dislocation + carry - cost.total_bps
            if self.require_positive_funding and tick.predicted_funding <= 0:
                reasons.append("funding<=0 (reżim v0)")
            if dislocation < self.min_dislocation_bps:
                reasons.append(f"dislocation {dislocation:.1f}<{self.min_dislocation_bps:.0f}")
            if net < self.min_edge_bps:
                reasons.append(f"net_edge {net:.1f}<{self.min_edge_bps:.0f}")
            edge_value = net
            ok_reason = (f"edge {net:+.1f}bps = disloc {dislocation:+.1f} "
                         f"+ carry {carry:+.1f} − cost {cost.total_bps:.1f}")
        else:  # carry
            if funding_bps < self.min_funding_bps:
                reasons.append(f"funding {funding_bps:+.2f}<{self.min_funding_bps:.1f}bps")
            if observed < -self.basis_floor_bps:
                reasons.append(f"basis {observed:+.1f} odwrócony (<−{self.basis_floor_bps:.0f})")
            edge_value = funding_bps
            ok_reason = (f"carry: funding {funding_bps:+.2f}bps/8h, basis {observed:+.1f}bps "
                         f"(koszt {cost.total_bps:.1f}bps amortyzowany w trzymaniu)")

        if reasons:
            state = SignalState.NO_TRADE
            reason = "; ".join(reasons)
        else:
            state = SignalState.EDGE_DETECTED
            reason = ok_reason

        return Signal(
            asset=tick.asset,
            ts=tick.ts,
            state=state,
            observed_basis_bps=observed,
            fair_basis_bps=fv.fair_basis_bps,
            dislocation_bps=dislocation,
            expected_net_edge_bps=edge_value,
            cost_bps=cost.total_bps,
            reason=reason,
        )

    # -- wiring z maszyną stanów EDGE_DETECTED/EDGE_LOST/NO_TRADE ------------ #
    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.MARKET_TICK, self._on_tick)

    async def _on_tick(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick) or self._bus is None:
            return
        sig = self.evaluate(tick)
        prev = self._last_state.get(tick.asset, SignalState.NO_TRADE)

        if sig.state == SignalState.EDGE_DETECTED:
            etype, out = EventType.EDGE_DETECTED, sig
        elif prev == SignalState.EDGE_DETECTED:
            etype, out = EventType.EDGE_LOST, replace(sig, state=SignalState.EDGE_LOST)
        else:
            etype, out = EventType.NO_TRADE_CONDITION, sig

        self._last_state[tick.asset] = sig.state
        await self._bus.publish(Event(etype, sig.ts, self.SOURCE, payload=out))
