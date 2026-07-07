"""Werdykt Tier B (pomiar): czy inne venue płacą funding trwale lepiej niż Binance?

Pobiera realną historię funding per aktywo z Binance + Bybit + OKX (publiczne,
read-only), annualizuje z REALNEGO interwału rozliczeń i drukuje tabelę
uplifta. Wynik zapisuje do VENUE_SCAN.md. Wymaga sieci do giełd — odpalaj na
maszynie właściciela (sandboxy zwykle blokują).

    python scripts/study_venues.py
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market.binance_public import fetch_funding_history_paged  # noqa: E402
from backend.adapters.market.venues import (  # noqa: E402
    fetch_bybit_funding,
    fetch_okx_funding,
    parse_binance_funding,
    venue_symbol,
)
from backend.core.types import ASSETS, Venue  # noqa: E402
from backend.research.multi_venue import compare, summarize  # noqa: E402


def main() -> None:
    comparisons = []
    for asset in ASSETS:
        by_venue = {}
        for venue, fetch in ((Venue.BINANCE, None), (Venue.BYBIT, fetch_bybit_funding),
                             (Venue.OKX, fetch_okx_funding)):
            sym = venue_symbol(venue, asset)
            if sym is None:
                continue
            try:
                if venue == Venue.BINANCE:
                    by_venue[venue] = parse_binance_funding(
                        fetch_funding_history_paged(sym, pages=2))
                else:
                    by_venue[venue] = fetch(sym)
            except Exception as exc:  # noqa: BLE001 — jedno venue nie kładzie werdyktu
                print(f"  ! {asset.value}@{venue.value}: {exc}")
        comparisons.append(compare(asset, by_venue))
        print(f"pobrano {asset.value}: " +
              ", ".join(f"{v.value}={len(p)}" for v, p in by_venue.items()))

    rows = summarize(comparisons)
    hdr = f"{'asset':7} {'Binance %/r':>11} {'najlepsze':>10} {'%/r':>7} {'uplift pp':>9}  venues"
    lines = [hdr, "-" * len(hdr)]
    for r in rows:
        base = f"{r['base_ann_pct']:.2f}" if r["base_ann_pct"] is not None else "—"
        best = f"{r['best_ann_pct']:.2f}" if r["best_ann_pct"] is not None else "—"
        up = f"{r['uplift_pp']:+.2f}" if r["uplift_pp"] is not None else "—"
        lines.append(f"{r['asset']:7} {base:>11} {r['best_venue'] or '—':>10} "
                     f"{best:>7} {up:>9}  {r['venues']}")
    print("\n".join(lines))

    today = datetime.date.today().isoformat()
    doc = (f"# VENUE SCAN — funding między giełdami (Tier B, pomiar)\n\n"
           f"> Wygenerowane {today} przez `scripts/study_venues.py`. Annualizacja z\n"
           f"> realnego interwału rozliczeń (mediana odstępów). Uplift BRUTTO — przed\n"
           f"> kosztami venue (fee/przelewy/spread) i ryzykiem kontrahenta.\n\n"
           "```\n" + "\n".join(lines) + "\n```\n\n"
           "Interpretacja: dodatni uplift ≥ kilka pp uzasadnia budowę egzekucji Tier B;\n"
           "uplift ~0 = Binance-only wystarcza i Tier B nie jest wart ryzyka venue.\n")
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "VENUE_SCAN.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"\nZapisano {out}")


if __name__ == "__main__":
    main()
