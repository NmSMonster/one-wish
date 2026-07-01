"""FundingWeightedSizer — Tier A: ważenie kapitału siłą sygnału funding.

Bez ważenia każda para dostaje ten sam płaski nominał niezależnie od tego, czy
funding wynosi 1%/rok (BTC) czy 28%/rok (DOGE) — a przecież zwrot z carry jest
wprost proporcjonalny do stawki funding. Realny ranking (UNIVERSE_SCAN.md, smoothed
net/rok): DOGE 27.7%, ZEC 23.9%, SOL 22.2%, XRP 21.9%, ETH 18.9%, BTC 10.5%. Równe
wagi na całość dają ~18%/rok; przesunięcie kapitału w stronę wyżej płacących aktywów
(przy tej samej selekcji i płynności) podnosi zwrot bez zmiany charakteru strategii
— wciąż market-neutral, wciąż ta sama giełda, wciąż ta sama logika wejścia/wyjścia.

Mechanizm: waga = funding_bps (forward, per okres 8h) / ref_funding_bps, przycięta
do [min_mult, max_mult]. Nominał = base_notional_usd × waga, przycięty do
[min_notional_usd, max_notional_usd] (drugi cap zwykle = RiskManager.max_trade_notional_usd,
żeby sizer nie proponował nominałów, które i tak zostaną odrzucone).

`ref_funding_bps` domyślnie ~1.5 bps/8h — to w przybliżeniu środek rozkładu
realnych stawek forward funding wśród aktywów w UNIVERSE_SCAN.md (BTC ~0.96,
ETH ~1.73, XRP ~2.0, SOL ~2.03, ZEC ~2.18, DOGE ~2.53 bps/8h, licząc
annualized_pct / 100 / 1095 × 1e4). Przy tym punkcie odniesienia BTC dostaje ~0.6×
bazowego nominału, DOGE ~1.7× — czyli kapitał realnie przesuwa się w stronę wyżej
płacących aktywów bez odcinania BTC/ETH całkowicie.

To NIE jest walidacja liczbowa — realny wpływ na roczny zwrot trzeba policzyć na
danych historycznych (`scripts/study_funding.py` w wariancie ważonym) i dopiero
wtedy zapisać nową liczbę w CARRY_VERDICT.md.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FundingWeightedSizer:
    base_notional_usd: float
    ref_funding_bps: float = 1.5
    min_mult: float = 0.5
    max_mult: float = 2.0
    min_notional_usd: float = 10.0
    max_notional_usd: float = float("inf")

    def weight(self, funding_bps: float) -> float:
        """Waga proporcjonalna do funding_bps względem punktu odniesienia,
        przycięta do [min_mult, max_mult]. funding_bps ≤ 0 → min_mult (nie
        nagradzamy kapitałem aktywa bez dodatniego forward funding)."""
        if self.ref_funding_bps <= 0 or funding_bps <= 0:
            return self.min_mult
        raw = funding_bps / self.ref_funding_bps
        return max(self.min_mult, min(self.max_mult, raw))

    def size(self, funding_bps: float) -> float:
        """Docelowy nominał pozycji dla danego forward funding (bps/8h)."""
        notional = self.base_notional_usd * self.weight(funding_bps)
        return max(self.min_notional_usd, min(self.max_notional_usd, notional))
