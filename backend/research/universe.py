"""Ranking uniwersum perpów po carry yield (selekcja-alpha).

Carry zarabia na funding — a różne perpy płacą bardzo różnie. Bot, który alokuje w
aktywa z trwale wysokim funding (przy odpowiedniej płynności), zarabia więcej. Ten
moduł rankinguje aktywa po realnym carry (smoothed net) i wystawia płynność jako
filtr ryzyka (cienkie alty = depeg/likwidacje/manipulacja).
"""
from __future__ import annotations

from dataclasses import dataclass

from .funding_study import analyze_funding, simulate_carry_smoothed


@dataclass
class AssetCarry:
    symbol: str
    n: int
    annualized_funding_pct: float
    pct_positive: float
    smoothed_net_pct: float
    quote_volume_usd: float


def rank_by_carry(items: list[tuple[str, list[float], float]],
                  round_trip_fee_bps: float = 18.6) -> list[AssetCarry]:
    """items: lista (symbol, stawki_funding, wolumen_24h_usd). Zwraca posortowane
    malejąco po smoothed net carry."""
    out: list[AssetCarry] = []
    for symbol, rates, volume in items:
        st = analyze_funding(rates)
        sm = simulate_carry_smoothed(rates, round_trip_fee_bps)
        out.append(AssetCarry(
            symbol=symbol, n=st.n, annualized_funding_pct=st.annualized_pct,
            pct_positive=st.pct_positive, smoothed_net_pct=sm.annualized_net_pct,
            quote_volume_usd=volume))
    out.sort(key=lambda a: a.smoothed_net_pct, reverse=True)
    return out


def filter_liquid(ranked: list[AssetCarry], min_volume_usd: float) -> list[AssetCarry]:
    """Odsiewa aktywa o zbyt niskim wolumenie (filtr ryzyka płynności)."""
    return [a for a in ranked if a.quote_volume_usd >= min_volume_usd]


def filter_quality(ranked: list[AssetCarry], min_n: int = 1000,
                   min_pct_positive: float = 75.0,
                   min_annualized_pct: float = 0.0) -> list[AssetCarry]:
    """Odsiewa aktywa o krótkiej historii, niestabilnym reżimie lub UJEMNEJ średniej
    funding. To ostatnie kluczowe: alt z 81% dodatnich, ale średnią −46%/rok ma
    gigantyczne ujemne spike'i (płaci ogromnie, gdy płaci) — smoothed net to ukrywa.
    Chroni przed gonieniem yield na świeżych/chimerycznych/jednostronnie-ujemnych."""
    return [a for a in ranked
            if a.n >= min_n
            and a.pct_positive >= min_pct_positive
            and a.annualized_funding_pct >= min_annualized_pct]
