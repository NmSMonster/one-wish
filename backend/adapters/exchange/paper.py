"""PaperBrokerAdapter (M7) — symulowany broker.

Udaje składanie zleceń: fill po cenie z poślizgiem (kup drożej, sprzedaj taniej),
nalicza prowizję, i opcjonalnie symuluje częściowe fille oraz odrzucenia.
Deterministyczny przez seed. Pozwala „handlować" bez pieniędzy (tryb domyślny).
"""
from __future__ import annotations

import random

from ...core.clock import Clock, RealClock
from ...core.types import Fill, Leg, OrderRequest, OrderStatus, Side
from .base import ExchangeAdapter, OrderResult


class PaperBrokerAdapter(ExchangeAdapter):
    name = "paper"
    is_live = False

    def __init__(
        self,
        *,
        fee_bps_spot: float = 7.5,
        fee_bps_perp: float = 1.8,
        slippage_bps: float = 1.0,
        reject_prob: float = 0.0,
        partial_prob: float = 0.0,
        reject_leg: Leg | None = None,
        seed: int = 0,
        clock: Clock | None = None,
    ) -> None:
        self.fee_bps_spot = fee_bps_spot
        self.fee_bps_perp = fee_bps_perp
        self.slippage_bps = slippage_bps
        self.reject_prob = reject_prob
        self.partial_prob = partial_prob
        self.reject_leg = reject_leg          # deterministyczne odrzucenie wskazanej nogi (testy atomowości)
        self._rng = random.Random(seed)
        self._clock = clock or RealClock()

    async def submit(self, req: OrderRequest) -> OrderResult:
        if self.reject_leg is not None and req.leg == self.reject_leg:
            return OrderResult(OrderStatus.REJECTED, [], f"symulowane odrzucenie nogi {req.leg.value}")
        if self._rng.random() < self.reject_prob:
            return OrderResult(OrderStatus.REJECTED, [], "symulowane odrzucenie")

        fill_qty = req.qty
        status = OrderStatus.FILLED
        if self._rng.random() < self.partial_prob:
            fill_qty = req.qty * 0.5
            status = OrderStatus.PARTIALLY_FILLED

        slip = self.slippage_bps / 1e4
        price = req.price * (1 + slip) if req.side == Side.BUY else req.price * (1 - slip)

        fee_bps = self.fee_bps_spot if req.leg == Leg.SPOT else self.fee_bps_perp
        fee = price * fill_qty * fee_bps / 1e4

        fill = Fill(
            client_order_id=req.client_order_id,
            asset=req.asset,
            leg=req.leg,
            side=req.side,
            price=price,
            qty=fill_qty,
            fee=fee,
            ts=req.ts,
        )
        return OrderResult(status, [fill])
