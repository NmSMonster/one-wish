"""Automat oceny etapu S2 (VALIDATION.md) — czyta bazę sesji, drukuje PASS/FAIL.

Kryteria akceptacji nie mogą zależeć od interpretacji człowieka po fakcie —
skrypt mierzy je wprost z trwałego audit trailu sesji paper-live:

    python scripts/validate_s2.py data/onewish_live.db [--min-hours 48]

Mierzy: długość sesji, medianę opóźnienia danych, awaryjne stopy (z powodami),
zgodność wejść z regułami (każde wejście musi mieć EDGE_DETECTED w audycie),
delta-neutralność odbudowanej księgi, aktywność funding. Wynik: tabela + kod
wyjścia 0 (PASS) / 1 (FAIL) — nadaje się do automatyzacji.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.execution import restore_book  # noqa: E402
from backend.storage import Database  # noqa: E402

_LAG_MAX_MEDIAN_MS = 2000.0     # kryterium 2.3
_DELTA_MAX_FRAC = 0.001         # kryterium 2.5: |delta| < 0.1% nominału


def _rows(db: Database, sql: str, args=()) -> list:
    return [dict(r) for r in db._conn.execute(sql, args).fetchall()]


def evaluate(db: Database, min_hours: float) -> list[dict]:
    checks: list[dict] = []

    def add(crit: str, ok: bool | None, detail: str) -> None:
        checks.append({"crit": crit, "ok": ok, "detail": detail})

    # 2.1 długość sesji (z zakresu ts ticków)
    span = _rows(db, "SELECT MIN(ts) AS a, MAX(ts) AS b, COUNT(*) AS n FROM ticks")[0]
    hours = ((span["b"] or 0) - (span["a"] or 0)) / 3600.0
    add("2.1 czas sesji", hours >= min_hours,
        f"{hours:.1f} h danych (próg {min_hours:.0f} h), ticków {span['n']}")

    # 2.2 emergency stopy — wypisz powody (heartbeat przy działającej sieci = FAIL 2.3)
    stops = _rows(db, "SELECT ts, payload FROM events WHERE type='EMERGENCY_STOP'")
    add("2.2 awaryjne stopy", None if stops else True,
        f"{len(stops)} × EMERGENCY_STOP" + (f"; pierwszy: {stops[0]['payload'][:120]}"
                                            if stops else " (brak)"))

    # 2.3 mediana opóźnienia danych
    lags = [r["data_lag_ms"] for r in _rows(db, "SELECT data_lag_ms FROM ticks")
            if r["data_lag_ms"] is not None]
    med = statistics.median(lags) if lags else float("inf")
    add("2.3 świeżość danych", med < _LAG_MAX_MEDIAN_MS,
        f"mediana lag {med:.0f} ms (próg {_LAG_MAX_MEDIAN_MS:.0f} ms)")

    # 2.4 każde wejście poprzedzone EDGE_DETECTED na TYM aktywie. Porządek po
    # rowid (kolejność wstawienia), nie po ts — źródła eventów bywają w różnych
    # domenach zegara (SimClock vs ts ticka) i ts ich nie porządkuje wiarygodnie.
    import json as _json

    def _asset_of(payload: str) -> str | None:
        try:
            p = _json.loads(payload) if payload else {}
            return p.get("asset")
        except (ValueError, TypeError):
            return None

    # UWAGA na zagnieżdżenie kaskady: EDGE→intent→risk→fill→OPEN dzieje się
    # WEWNĄTRZ obsługi eventu EDGE, więc wiersz OPEN trafia do DB kilka rowidów
    # PRZED wierszem swojego EDGE. Stąd okno: EDGE tego aktywa musi istnieć
    # najpóźniej ~50 wierszy po OPEN (kaskada to kilka-kilkanaście eventów).
    _CASCADE_WINDOW = 50
    opens = _rows(db, "SELECT rowid AS rid, payload FROM events "
                      "WHERE type='POSITION_OPENED' ORDER BY rowid")
    edges = _rows(db, "SELECT rowid AS rid, payload FROM events "
                      "WHERE type='EDGE_DETECTED' ORDER BY rowid")
    edge_rids: dict[str, list] = {}
    for e in edges:
        a = _asset_of(e["payload"])
        if a is not None:
            edge_rids.setdefault(a, []).append(e["rid"])
    unexplained = 0
    for o in opens:
        a = _asset_of(o["payload"])
        rids = edge_rids.get(a, [])
        if a is None or not any(r <= o["rid"] + _CASCADE_WINDOW for r in rids):
            unexplained += 1
    add("2.4 wejścia z sygnału", unexplained == 0,
        f"{len(opens)} wejść, {unexplained} bez sygnału EDGE na aktywie (okno kaskady)")

    # 2.5 delta-neutralność: odbuduj księgę i sprawdź otwarte pary
    book = restore_book(db)
    worst = 0.0
    for asset, pos in book.positions.items():
        if pos.is_open and abs(pos.spot_qty) > 0:
            frac = abs(pos.net_delta) / abs(pos.spot_qty)
            worst = max(worst, frac)
    add("2.5 delta-neutralność", worst < _DELTA_MAX_FRAC,
        f"najgorsza |delta| = {worst:.5%} nominału (próg {_DELTA_MAX_FRAC:.1%})")

    # 2.6/2.8 sanity funding i rozkład PnL
    fundings = _rows(db, "SELECT COUNT(*) AS n FROM events WHERE type='FUNDING_ACCRUED'")[0]["n"]
    pnl_last = _rows(db, "SELECT * FROM pnl ORDER BY ts DESC LIMIT 1")
    if pnl_last:
        p = pnl_last[0]
        detail = (f"{fundings} rozliczeń funding; ostatni PnL: realized {p['realized']:+.2f} "
                  f"unrealized {p['unrealized']:+.2f} funding {p['funding_collected']:+.2f} "
                  f"fees {p['fees_paid']:.2f}")
        sane = fundings == 0 or p["funding_collected"] != 0.0
    else:
        detail = f"{fundings} rozliczeń funding; brak wpisów PnL"
        sane = fundings == 0
    add("2.6/2.8 funding+PnL", sane, detail)

    return checks


def main() -> None:
    ap = argparse.ArgumentParser(description="Ocena S2 (VALIDATION.md) z bazy sesji")
    ap.add_argument("db", help="ścieżka bazy sesji (np. data/onewish_live.db)")
    ap.add_argument("--min-hours", type=float, default=48.0)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(f"Brak pliku bazy: {args.db}")
        raise SystemExit(2)

    checks = evaluate(Database(args.db), args.min_hours)
    width = max(len(c["crit"]) for c in checks)
    failed = False
    for c in checks:
        mark = "PASS" if c["ok"] else ("INFO" if c["ok"] is None else "FAIL")
        failed = failed or (c["ok"] is False)
        print(f"{c['crit']:<{width}}  [{mark}]  {c['detail']}")
    print()
    print("WYNIK S2:", "FAIL — patrz wyżej" if failed else
          "PASS (kryteria mierzalne z bazy; 2.7 budżet sprawdź w raporcie sesji)")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
