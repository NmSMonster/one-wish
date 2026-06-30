"""Skanuje uniwersum perpów Binance i rankinguje po carry yield (selekcja-alpha).

Pobiera top perpy po 24h wolumenie, liczy realny carry z historii funding każdego,
sortuje i wypisuje + zapisuje UNIVERSE_SCAN.md. Pomaga wybrać najlepiej płacące,
płynne aktywa do carry.

Użycie:
    python scripts/scan_universe.py --top 25
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market.binance_public import (  # noqa: E402
    FUT_BASE,
    _http_get_json,
    fetch_funding_history_paged,
)
from backend.research.funding_study import rates_from_history  # noqa: E402
from backend.research.universe import filter_liquid, filter_quality, rank_by_carry  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUND_TRIP_FEE_BPS = 18.6
MIN_VOLUME_USD = 50_000_000.0  # filtr płynności (24h quote volume)


def top_perps(n: int) -> list[tuple[str, float]]:
    tickers = _http_get_json(FUT_BASE + "/fapi/v1/ticker/24hr")
    rows = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            rows.append((sym, float(t.get("quoteVolume", 0.0))))
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:n]


def main() -> None:
    ap = argparse.ArgumentParser(description="One Wish — skan uniwersum carry")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--pages", type=int, default=3, help="stron historii funding/aktywo")
    args = ap.parse_args()

    print(f"Pobieram top {args.top} perpów po wolumenie...")
    universe = top_perps(args.top)
    items = []
    for sym, vol in universe:
        try:
            hist = fetch_funding_history_paged(sym, pages=args.pages)
            items.append((sym, rates_from_history(hist), vol))
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: pomijam ({exc})")

    ranked = rank_by_carry(items, ROUND_TRIP_FEE_BPS)
    liquid = filter_liquid(ranked, MIN_VOLUME_USD)
    robust = filter_quality(liquid, min_n=1000, min_pct_positive=75.0, min_annualized_pct=5.0)

    print("\nWSZYSTKIE PŁYNNE (po yield — uwaga: czubek bywa świeży/ryzykowny):\n")
    print(_format(liquid[:20]))
    print("\n⭐ ROBUST (historia ≥ ~rok i ≥75% rozliczeń dodatnich) — KANDYDACI BOTA:\n")
    print(_format(robust[:12]))
    print(f"\nFiltry: wolumen ≥ ${MIN_VOLUME_USD/1e6:.0f}M, historia ≥ 1000 rozliczeń, ≥75% dodatnich.")
    print("UWAGA: wyższy funding alta = wyższe ryzyko (depeg/likwidacje/manipulacja); margin watchdog chroni.")

    _write_doc(_format(robust[:12]))


def _format(rows) -> str:
    header = (f"{'#':>2} {'symbol':12} {'n':>4} {'funding/rok':>11} {'%dod':>6} "
              f"{'carry net/rok':>13} {'24h vol':>12}")
    lines = [header, "-" * len(header)]
    for i, a in enumerate(rows, 1):
        lines.append(f"{i:2d} {a.symbol:12} {a.n:4d} {a.annualized_funding_pct:10.2f}% "
                     f"{a.pct_positive:5.1f}% {a.smoothed_net_pct:12.2f}% ${a.quote_volume_usd/1e6:9.0f}M")
    return "\n".join(lines)


def _write_doc(table: str) -> None:
    today = datetime.date.today().isoformat()
    doc = f"""# Skan uniwersum — ranking carry

> {today}, `scripts/scan_universe.py`. Carry net = smoothed (po prowizjach), na
> historii funding Binance. Filtr płynności: 24h volume ≥ ${MIN_VOLUME_USD/1e6:.0f}M.

```
{table}
```

Selekcja: bot powinien trzymać aktywa z TOPU (wysoki, trwały funding) przy
wystarczającej płynności. Wyższy funding alta zwykle = wyższe ryzyko (depeg,
kaskady likwidacji, manipulacja) — dlatego płynność jest twardym filtrem, a margin
watchdog i niska dźwignia chronią nogę perp.
"""
    with open(os.path.join(ROOT, "UNIVERSE_SCAN.md"), "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("\nZapisano -> UNIVERSE_SCAN.md")


if __name__ == "__main__":
    main()
