"""StrategyPolicy — reguły wejścia/wyjścia.

Dwa tryby:
- "carry" (domyślny, właściwy dla strategii basis/funding): otwórz parę
  delta-neutral na korzystnym wejściu (EDGE_DETECTED) i TRZYMAJ ją, inkasując
  funding co rozliczenie; zamknij dopiero, gdy nośność znika (funding ≤ próg) albo
  basis odwróci się poza stop. NIE zamykamy na zaniku dyslokacji — to normalne i
  oczekiwane, a churn tylko pali prowizje.
- "scalp": otwórz na EDGE_DETECTED, zamknij na EDGE_LOST (szybki obrót na samej
  dyslokacji). Zostawione do porównań/backtestu.

Polityka pilnuje, by nie dublować wejść ani wyjść (śledzi pozycje trzymane,
„w locie" i „w zamykaniu").
"""
from __future__ import annotations

from ..core.bus import EventBus
from ..core.events import Event, EventType
from ..core.types import MarketTick, Position, Signal, TradeIntent


class StrategyPolicy:
    SOURCE = "strategy_policy"

    def __init__(self, *, notional_usd: float = 200.0, bus: EventBus | None = None,
                 mode: str = "carry", funding_exit: float = 0.0,
                 basis_stop_bps: float = -15.0, funding_ema_alpha: float = 0.05,
                 sizer=None) -> None:
        self.notional_usd = notional_usd
        self._bus = bus
        self.mode = mode
        # Tier A: opcjonalny sizer waży nominał siłą sygnału (funding_bps) zamiast
        # płaskiego notional_usd na każdą parę. None = stare zachowanie (płaski nominał).
        self.sizer = sizer
        self.funding_exit = funding_exit          # zamknij, gdy WYGŁADZONY funding ≤ to
        self.basis_stop_bps = basis_stop_bps      # zamknij, gdy observed basis ≤ to (perp za tani)
        # EMA forward funding: nie wychodzimy na pojedynczym ujemnym ticku (churn pali
        # prowizje 18.6 bps; werdykt na realnym roku dowiódł, że smoothed >> pos-only).
        # alpha mały = stickier (dłuższe efektywne okno). 1.0 = brak wygładzania.
        self.funding_ema_alpha = funding_ema_alpha
        self._funding_ema: dict = {}
        self._holding: set = set()
        self._inflight: set = set()
        self._closing: set = set()

    def restore_holding(self, asset) -> None:
        """Rejestruje pozycję ODZYSKANĄ po restarcie: polityka nie dubluje wejścia
        i znów nadzoruje wyjście (flip funding EMA / stop basis) dla tej pary."""
        self._holding.add(asset)

    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(EventType.EDGE_DETECTED, self._on_edge)
        bus.subscribe(EventType.EDGE_LOST, self._on_edge_lost)
        bus.subscribe(EventType.POSITION_OPENED, self._on_opened)
        bus.subscribe(EventType.POSITION_CLOSED, self._on_closed)
        bus.subscribe(EventType.RISK_REJECTED, self._on_rejected)
        if self.mode == "carry":
            bus.subscribe(EventType.MARKET_TICK, self._on_tick_carry)

    async def _emit_open(self, asset, ts, edge, reason) -> None:
        # `edge` w trybie carry to forward funding_bps (Signal.expected_net_edge_bps) —
        # dokładnie to, czym sizer waży kapitał. W trybie dislocation `edge` ma inną
        # semantykę (net edge disloc+carry-cost); sizer jest wtedy opt-in świadomie.
        assert self._bus is not None          # wołane tylko z handlerów po attach()
        notional = self.sizer.size(edge) if self.sizer is not None else self.notional_usd
        self._inflight.add(asset)
        await self._bus.publish(Event(EventType.TRADE_INTENT, ts, self.SOURCE,
                                      payload=TradeIntent(asset, ts, "OPEN", notional,
                                                          edge, reason)))

    async def _emit_close(self, asset, ts, reason) -> None:
        assert self._bus is not None          # wołane tylko z handlerów po attach()
        self._closing.add(asset)
        await self._bus.publish(Event(EventType.TRADE_INTENT, ts, self.SOURCE,
                                      payload=TradeIntent(asset, ts, "CLOSE", 0.0, 0.0, reason)))

    async def _on_edge(self, event: Event) -> None:
        sig = event.payload
        if not isinstance(sig, Signal) or self._bus is None:
            return
        if sig.asset in self._holding or sig.asset in self._inflight:
            return
        await self._emit_open(sig.asset, sig.ts, sig.expected_net_edge_bps, sig.reason)

    async def _on_edge_lost(self, event: Event) -> None:
        if self.mode != "scalp":
            return  # carry: zanik dyslokacji to nie powód do wyjścia
        sig = event.payload
        if not isinstance(sig, Signal) or self._bus is None:
            return
        if sig.asset in self._holding and sig.asset not in self._closing:
            await self._emit_close(sig.asset, sig.ts, "edge lost (scalp)")

    async def _on_tick_carry(self, event: Event) -> None:
        tick = event.payload
        if not isinstance(tick, MarketTick) or self._bus is None:
            return
        asset = tick.asset

        # aktualizuj EMA forward funding (zawsze, by sygnał był rozgrzany)
        prev = self._funding_ema.get(asset)
        a = self.funding_ema_alpha
        ema = tick.predicted_funding if prev is None else a * tick.predicted_funding + (1 - a) * prev
        self._funding_ema[asset] = ema

        if asset not in self._holding or asset in self._closing:
            return
        if ema <= self.funding_exit:
            await self._emit_close(asset, tick.ts,
                                   f"funding(EMA) {ema * 1e4:+.2f}bps ≤ próg (reżim się odwrócił)")
        elif tick.basis_bps <= self.basis_stop_bps:
            await self._emit_close(asset, tick.ts,
                                   f"basis {tick.basis_bps:+.1f}bps ≤ stop {self.basis_stop_bps:.0f}")

    async def _on_opened(self, event: Event) -> None:
        p = event.payload
        if isinstance(p, Position):
            self._holding.add(p.asset)
            self._inflight.discard(p.asset)

    async def _on_closed(self, event: Event) -> None:
        p = event.payload
        if isinstance(p, Position):
            self._holding.discard(p.asset)
            self._inflight.discard(p.asset)
            self._closing.discard(p.asset)

    async def _on_rejected(self, event: Event) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        intent = payload.get("intent")
        if isinstance(intent, TradeIntent):
            self._inflight.discard(intent.asset)
