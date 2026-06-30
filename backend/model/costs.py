"""CostModel — jawny koszt round-trip pary delta-neutral, w bps.

Round-trip = otwarcie (kup spot + sprzedaj perp) + zamknięcie (sprzedaj spot +
kup perp), czyli 2 transakcje spot + 2 transakcje perp.

UCZCIWA UWAGA: prowizje spot na Binance są wysokie (rzędu 0.1%). To jest główny
zabójca tej strategii — dlatego liczymy koszt od początku i z zapasem. Wartości
domyślne zakładają maker z rabatem BNB; realne stawki zależą od poziomu VIP i
konfigurujemy je per środowisko.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.types import CostEstimate, MarketTick


@dataclass
class CostModel:
    spot_fee_bps: float = 7.5        # prowizja na nogę spot (maker + rabat BNB)
    perp_fee_bps: float = 1.8        # prowizja na nogę perp (maker)
    slippage_coeff_bps: float = 6.0  # poślizg na nogę przy notional == dostępna głębokość
    include_spread: bool = True

    def round_trip_cost_bps(self, tick: MarketTick, notional_usd: float) -> CostEstimate:
        # 2 nogi × (wejście + wyjście) = ×2 na każdą prowizję
        fees_bps = 2.0 * (self.spot_fee_bps + self.perp_fee_bps)

        spread_bps = (tick.spot_spread_bps + tick.perp_spread_bps) if self.include_spread else 0.0

        spot_impact = self.slippage_coeff_bps * (notional_usd / max(1.0, tick.spot_depth_usd))
        perp_impact = self.slippage_coeff_bps * (notional_usd / max(1.0, tick.perp_depth_usd))
        slippage_bps = 2.0 * (spot_impact + perp_impact)

        return CostEstimate(fees_bps=fees_bps, slippage_bps=slippage_bps, spread_bps=spread_bps)
