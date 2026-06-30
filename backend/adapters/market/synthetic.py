"""SyntheticSource — deterministyczny generator realistycznego strumienia rynku.

Spot porusza się losowym błądzeniem; perp wycenia basis = fair_basis + szum, z
okresowo wstrzykiwanymi DYSLOKACJAMI (perp chwilowo zbyt drogi po ruchu spotu),
które potem zanikają. Dzięki temu Repricing Detector ma realne okazje do
wykrycia, a backtest/M3.5 mogą działać bez sieci i powtarzalnie.
"""
from __future__ import annotations

import asyncio
import math
import random
from collections.abc import AsyncIterator

from ...core.types import ASSETS, Asset, MarketTick
from .base import MarketSource

_DEFAULT_PRICES = {
    Asset.BTC: 60_000.0,
    Asset.ETH: 3_000.0,
    Asset.SOL: 150.0,
    Asset.XRP: 0.60,
}


class SyntheticSource(MarketSource):
    def __init__(
        self,
        *,
        assets: tuple[Asset, ...] = ASSETS,
        steps: int = 300,
        dt: float = 1.0,
        start_ts: float = 1_700_000_000.0,
        seed: int = 7,
        funding_rate: float = 0.0002,      # 0.02% / 8h → reżim funding dodatni; funding_bps == fair_basis
        funding_period_s: float = 8 * 3600.0,  # cykl funding (krótki w testach/demo, by inkasować nośność)
        fair_basis_bps: float = 2.0,
        vol_bps: float = 8.0,
        noise_bps: float = 1.5,
        spread_bps: float = 1.5,
        depth_usd: float = 750_000.0,
        lag_ms: float = 6.0,
        dislocation_every: int = 31,
        dislocation_bps: float = 60.0,   # SYMULACJA: wyolbrzymione, by ćwiczyć ścieżki EDGE; nie jest to teza o zysku
        dislocation_decay: float = 0.6,
        pace: bool = False,              # demo GUI: realny odstęp między krokami
        pace_dt: float = 0.05,
    ) -> None:
        self.assets = assets
        self.pace = pace
        self.pace_dt = pace_dt
        self.steps = steps
        self.dt = dt
        self.start_ts = start_ts
        self.funding_rate = funding_rate
        self.funding_period_s = funding_period_s
        self.fair_basis_bps = fair_basis_bps
        self.vol_bps = vol_bps
        self.noise_bps = noise_bps
        self.spread_bps = spread_bps
        self.depth_usd = depth_usd
        self.lag_ms = lag_ms
        self.dislocation_every = dislocation_every
        self.dislocation_bps = dislocation_bps
        self.dislocation_decay = dislocation_decay
        self._rng = random.Random(seed)
        self._prices = {a: _DEFAULT_PRICES.get(a, 100.0) for a in assets}
        self._disloc = {a: 0.0 for a in assets}

    async def ticks(self) -> AsyncIterator[MarketTick]:
        funding_period = self.funding_period_s
        for step in range(self.steps):
            ts = self.start_ts + step * self.dt
            next_funding = self.start_ts + math.ceil((step * self.dt + 1) / funding_period) * funding_period
            for offset, asset in enumerate(self.assets):
                # losowe błądzenie ceny spot/indeksu
                shock = self._rng.gauss(0.0, 1.0) * self.vol_bps / 1e4
                self._prices[asset] *= math.exp(shock)
                index = self._prices[asset]

                # wstrzyknięcie dyslokacji (rozłożone w czasie per aktywo)
                if step > 0 and (step + offset * 7) % self.dislocation_every == 0:
                    self._disloc[asset] += self.dislocation_bps
                self._disloc[asset] *= self.dislocation_decay

                noise = self._rng.gauss(0.0, 1.0) * self.noise_bps
                basis_bps = self.fair_basis_bps + noise + self._disloc[asset]
                perp = index * (1.0 + basis_bps / 1e4)
                spot = index  # spot ≈ index w syntetyku

                half_spot = spot * self.spread_bps / 1e4 / 2.0
                half_perp = perp * self.spread_bps / 1e4 / 2.0

                yield MarketTick(
                    asset=asset,
                    ts=ts,
                    spot=spot,
                    perp=perp,
                    index=index,
                    funding_rate=self.funding_rate,
                    predicted_funding=self.funding_rate,
                    next_funding_ts=next_funding,
                    spot_bid=spot - half_spot,
                    spot_ask=spot + half_spot,
                    perp_bid=perp - half_perp,
                    perp_ask=perp + half_perp,
                    spot_depth_usd=self.depth_usd,
                    perp_depth_usd=self.depth_usd,
                    data_lag_ms=self.lag_ms,
                )
            if self.pace:
                await asyncio.sleep(self.pace_dt)
