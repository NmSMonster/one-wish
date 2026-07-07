"""Odbudowa księgi pozycji po restarcie (crash-safe recovery).

Księga (PositionBook) żyje w pamięci — pad procesu z otwartymi pozycjami
oznaczałby, że po restarcie bot NIE WIE o własnych pozycjach na giełdzie
(hedge zostaje bez nadzoru: bez margin watchdoga, bez wyjścia na flip funding).

Źródłem prawdy jest trwały audit trail w sqlite: tabela `fills` + zdarzenia
FUNDING_ACCRUED. Odtworzenie = chronologiczny replay tych zapisów przez tę samą
logikę księgowania (`apply_fill`/`add_funding`), której używa żywy bot — stan po
odbudowie jest z definicji spójny z tym, co bot by miał, gdyby nie padł.

W live po odbudowie stan porównuje się z giełdą (`ExchangeAdapter.reconcile()` /
`account_state()`); w paper odbudowa wystarcza w całości.
"""
from __future__ import annotations

import logging

from ..core.types import Asset, Fill, Leg, Side
from ..storage.db import Database
from .book import PositionBook

log = logging.getLogger("onewish.recovery")


def restore_book(db: Database, book: PositionBook | None = None) -> PositionBook:
    """Odtwarza księgę z trwałego audit trailu (fille + funding, chronologicznie).

    Zwraca księgę (nową albo podaną — np. `pipeline.book`, żeby cały pipeline
    widział odzyskany stan). Nieznane aktywa (spoza enuma) są pomijane z logiem —
    stary plik DB nie może wywrócić nowej wersji bota.
    """
    book = book if book is not None else PositionBook()

    entries: list[tuple[float, int, str, dict]] = []
    for i, f in enumerate(db.fills_all()):
        entries.append((float(f["ts"]), i, "fill", f))
    for i, fe in enumerate(db.funding_events()):
        entries.append((float(fe["ts"]), i, "funding", fe))
    entries.sort(key=lambda e: (e[0], e[1]))

    applied_fills = applied_funding = skipped = 0
    for ts, _, kind, data in entries:
        try:
            asset = Asset(data["asset"])
        except (ValueError, KeyError):
            skipped += 1
            continue
        if kind == "fill":
            book.apply_fill(Fill(
                client_order_id=str(data["client_order_id"]),
                asset=asset,
                leg=Leg(data["leg"]),
                side=Side(data["side"]),
                price=float(data["price"]),
                qty=float(data["qty"]),
                fee=float(data["fee"]),
                ts=ts,
            ), ts)
            applied_fills += 1
        else:
            book.add_funding(asset, float(data.get("amount", 0.0)))
            applied_funding += 1

    open_assets = [a.value for a, p in book.positions.items() if p.is_open]
    if applied_fills or applied_funding:
        log.warning("RECOVERY: odbudowano księgę z %d filli + %d rozliczeń funding "
                    "(pominięto %d); otwarte pozycje: %s",
                    applied_fills, applied_funding, skipped, open_assets or "brak")
    return book
