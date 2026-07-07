"""Porównanie funding między giełdami (Tier B — warstwa POMIAROWA).

Odpowiada na pytanie, od którego zależy sens Tier B: czy short perp na innym
venue (Bybit/OKX/...) płaci na tym samym aktywie trwale więcej niż Binance —
i o ile. Metodologia jak w Tier A: annualizacja z REALNEGO interwału rozliczeń
(wyprowadzonego z timestampów — 8h vs 4h vs 1h; złe założenie interwału
zaniżało/zawyżało liczby 2–8×), średnia po oknie, zero look-ahead.

Czysta matematyka — dane wstrzykuje wołający (skrypt/testy)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..adapters.market.venues import FundingPoint
from ..core.types import Asset, Venue

_HOURS_PER_YEAR = 24.0 * 365.0


def infer_settles_per_year(points: list[FundingPoint]) -> float:
    """Realny interwał rozliczeń z mediany odstępów timestampów (jak w Tier A)."""
    if len(points) < 3:
        return _HOURS_PER_YEAR / 8.0                      # bez danych: załóż 8h
    gaps = [b.ts - a.ts for a, b in zip(points, points[1:]) if b.ts > a.ts]
    if not gaps:
        return _HOURS_PER_YEAR / 8.0
    med_h = statistics.median(gaps) / 3600.0
    if med_h <= 0:
        return _HOURS_PER_YEAR / 8.0
    return _HOURS_PER_YEAR / med_h


@dataclass(frozen=True)
class VenueQuote:
    venue: Venue
    asset: Asset
    mean_rate: float          # średnia stawka na rozliczenie (ułamek)
    settles_per_year: float
    n_points: int

    @property
    def annualized_pct(self) -> float:
        """Annualizowany funding (%/rok) — dla SHORTA to przychód przy rate>0."""
        return self.mean_rate * self.settles_per_year * 100.0


def quote(venue: Venue, asset: Asset, points: list[FundingPoint]) -> VenueQuote | None:
    if not points:
        return None
    return VenueQuote(venue=venue, asset=asset,
                      mean_rate=statistics.fmean(p.rate for p in points),
                      settles_per_year=infer_settles_per_year(points),
                      n_points=len(points))


@dataclass
class VenueComparison:
    asset: Asset
    quotes: list[VenueQuote] = field(default_factory=list)

    @property
    def best(self) -> VenueQuote | None:
        """Venue najlepsze dla NOGI SHORT = najwyższy annualizowany funding."""
        return max(self.quotes, key=lambda q: q.annualized_pct, default=None)

    def uplift_vs(self, base: Venue = Venue.BINANCE) -> float | None:
        """Przewaga najlepszego venue nad bazowym, w pkt proc. rocznie.
        None = brak notowania na venue bazowym (nie ma do czego porównać)."""
        base_q = next((q for q in self.quotes if q.venue == base), None)
        best_q = self.best
        if base_q is None or best_q is None:
            return None
        return best_q.annualized_pct - base_q.annualized_pct


def compare(asset: Asset, by_venue: dict[Venue, list[FundingPoint]]) -> VenueComparison:
    cmpn = VenueComparison(asset=asset)
    for venue, points in by_venue.items():
        q = quote(venue, asset, points)
        if q is not None:
            cmpn.quotes.append(q)
    return cmpn


def summarize(comparisons: list[VenueComparison], base: Venue = Venue.BINANCE) -> list[dict]:
    """Wiersze werdyktu per aktywo: kto płaci najlepiej i o ile więcej niż baza.
    UWAGA interpretacyjna: uplift brutto — realny Tier B musi jeszcze odjąć koszty
    venue (fee, przelewy, spread wyjścia) i ryzyko kontrahenta; ta tabela mówi,
    CZY jest o co grać, nie że wolno wchodzić."""
    rows = []
    for c in comparisons:
        best = c.best
        base_q = next((q for q in c.quotes if q.venue == base), None)
        rows.append({
            "asset": c.asset.value,
            "base_ann_pct": base_q.annualized_pct if base_q else None,
            "best_venue": best.venue.value if best else None,
            "best_ann_pct": best.annualized_pct if best else None,
            "uplift_pp": c.uplift_vs(base),
            "venues": {q.venue.value: round(q.annualized_pct, 2) for q in c.quotes},
        })
    rows.sort(key=lambda r: (r["uplift_pp"] is None, -(r["uplift_pp"] or 0.0)))
    return rows
