"""Werdykt CARRY na REALNEJ historii funding Binance (~rok, natychmiast).

Pobiera historię stawek funding dla BTC/ETH/SOL/XRP i liczy, czy delta-neutralny
carry (inkasowanie funding) miał przewagę po prowizjach — w trzech politykach:
always-in, positive-only (churn) i smoothed (trzymaj przez szum). Werdykt na
prawdziwych danych, bez czekania na zbieranie ticków. Zapisuje CARRY_VERDICT.md.
"""
from __future__ import annotations

import datetime
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market.binance_public import fetch_funding_history_paged  # noqa: E402
from backend.core.types import ASSETS, BINANCE_SYMBOL  # noqa: E402
from backend.research.funding_study import (  # noqa: E402
    analyze_funding,
    rates_from_history,
    simulate_carry_always_in,
    simulate_carry_positive_only,
    simulate_carry_smoothed,
)
from backend.research.portfolio_study import (  # noqa: E402
    equal_weights,
    funding_weights,
    simulate_portfolio,
    top_n_assets,
)

ROUND_TRIP_FEE_BPS = 18.6  # 2×(spot 7.5 + perp 1.8) — maker z rabatem BNB
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    rows = []
    rates_by_asset: dict[str, list[float]] = {}
    for asset in ASSETS:
        symbol = BINANCE_SYMBOL[asset]
        try:
            history = fetch_funding_history_paged(symbol, pages=4)
        except Exception as exc:  # noqa: BLE001
            print(f"{asset.value}: blad sieci: {exc}")
            continue
        rates = rates_from_history(history)
        rates_by_asset[asset.value] = rates
        rows.append({
            "asset": asset.value,
            "stats": analyze_funding(rates),
            "always": simulate_carry_always_in(rates, ROUND_TRIP_FEE_BPS),
            "pos": simulate_carry_positive_only(rates, ROUND_TRIP_FEE_BPS),
            "smooth": simulate_carry_smoothed(rates, ROUND_TRIP_FEE_BPS),
        })

    header = (f"{'asset':5} {'n':>4} {'~dni':>5} {'sr/8h':>7} {'%dod':>6} "
              f"{'brutto/rok':>11} {'always':>8} {'pos-only':>9} {'smoothed':>9}")
    lines = [header, "-" * len(header)]
    for r in rows:
        st = r["stats"]
        lines.append(
            f"{r['asset']:5} {st.n:4d} {st.n/3:5.0f} {st.mean_bps:6.3f}b {st.pct_positive:5.1f}% "
            f"{st.annualized_pct:10.2f}% {r['always'].annualized_net_pct:7.2f}% "
            f"{r['pos'].annualized_net_pct:8.2f}% {r['smooth'].annualized_net_pct:8.2f}%")

    # werdykt portfelowy: bot wchodzi tylko w aktywa z dodatnim funding (require_positive_funding)
    traded = [r for r in rows if r["stats"].annualized_pct > 0]
    if traded:
        avg_smooth = sum(r["smooth"].annualized_net_pct for r in traded) / len(traded)
        names = ", ".join(r["asset"] for r in traded)
        verdict_line = (f"PORTFEL BOTA (tylko dodatni funding: {names}): "
                        f"smoothed ~{avg_smooth:+.2f}%/rok")
    else:
        verdict_line = "PORTFEL BOTA: brak aktywów z dodatnim funding — carry bez sensu w tym oknie"

    body = "\n".join(lines)
    print("WERDYKT CARRY — realna historia funding Binance "
          f"(round-trip {ROUND_TRIP_FEE_BPS} bps)\n")
    print(body)
    print("\n" + verdict_line)
    print("\nKLUCZ: always (trzymaj zawsze) vs pos-only (wyjdz na kazdym minusie = churn) "
          "vs smoothed (trzymaj przez szum).")
    print("Smoothed >> pos-only dowodzi: NIE wychodzic na pojedynczym ujemnym funding.")

    portfolio_body = _portfolio_section(rows, rates_by_asset)
    if portfolio_body:
        print("\n" + portfolio_body)

    _write_doc(body, verdict_line, portfolio_body)


def _portfolio_section(rows: list[dict], rates_by_asset: dict[str, list[float]]) -> str:
    """Werdykt Tier A: równe wagi vs top-N vs ważenie funding, z ryzykiem (maxDD,
    Calmar). Wagi liczy PRODUKCYJNA funkcja bota (FundingWeightedSizer.weight) —
    badanie i egzekucja są spójne z definicji."""
    traded = [r["asset"] for r in rows if r["stats"].annualized_pct > 0]
    if len(traded) < 2:
        return ""
    mean_bps = {r["asset"]: r["stats"].mean_bps for r in rows if r["asset"] in traded}
    smooth_pct = {r["asset"]: r["smooth"].annualized_net_pct for r in rows if r["asset"] in traded}
    top4 = top_n_assets(smooth_pct, min(4, len(traded)))

    variants = [
        ("rowne wagi (wszystkie dodatnie)", equal_weights(traded)),
        ("rowne wagi top-4 po carry", equal_weights(top4)),
        ("wazone funding (wszystkie)", funding_weights(mean_bps)),
        ("wazone funding top-4", funding_weights({k: mean_bps[k] for k in top4})),
    ]
    header = f"{'wariant':32} {'zwrot/rok':>10} {'maxDD':>7} {'Calmar':>7}  sklad"
    out = ["PORTFEL TIER A — alokacja vs zwrot i ryzyko (polityka smoothed):",
           header, "-" * len(header)]
    for label, weights in variants:
        try:
            res = simulate_portfolio(rates_by_asset, weights,
                                     round_trip_fee_bps=ROUND_TRIP_FEE_BPS, label=label)
        except ValueError as exc:
            out.append(f"{label:32} pominieto ({exc})")
            continue
        calmar = f"{res.calmar:6.1f}" if res.calmar is not None else "   inf"
        skład = " ".join(f"{k}:{w:.0%}" for k, w in sorted(res.weights.items(), key=lambda kv: -kv[1]))
        out.append(f"{label:32} {res.annualized_net_pct:9.2f}% {res.max_drawdown_pct:6.2f}% "
                   f"{calmar}  {skład}")
    out.append("")
    out.append("Roznica 'wazone' vs 'rowne wagi' = zmierzony (nie obiecany) efekt Tier A.")
    out.append("Dopiero TE liczby wolno wpisac jako nowy target zwrotu.")
    return "\n".join(out)


def _write_doc(table: str, verdict_line: str, portfolio_body: str = "") -> None:
    today = datetime.date.today().isoformat()
    portfolio_block = f"\n```\n{portfolio_body}\n```\n" if portfolio_body else ""
    doc = f"""# CARRY — werdykt na realnej historii funding

> Wygenerowane {today} przez `scripts/study_funding.py` na prawdziwej historii
> stawek funding Binance (endpoint fundingRate). round-trip fee = {ROUND_TRIP_FEE_BPS} bps.

```
{table}
```

{verdict_line}
{portfolio_block}

## Co z tego wynika

- **always** ≈ annualizowany funding minus jeden round-trip (drag ~0.19%/rok) —
  realny sufit carry dla aktywa, jeśli trzymasz cały czas.
- **pos-only** (wyjście na każdym ujemnym funding) tonie przez **churn**: każdy flip
  to round-trip 18.6 bps, a ujemny funding kosztuje ułamek bps. To była wada polityki.
- **smoothed** (wyjście dopiero gdy reżim funding się odwraca) odzyskuje większość
  carry — to właściwa polityka i taka jest teraz w bocie.

## Wniosek

Carry to **niskie, ale realne** wynagrodzenie delta-neutral na aktywach z trwale
dodatnim funding (zwykle BTC/ETH); na aktywach z ujemnym funding (często SOL/XRP w
tym oknie) carry NIE ma sensu i bot ich nie bierze (require_positive_funding).
To strategia typu „stabilny rynkowo-neutralny yield", nie wysokozwrotna — i jako
taka jest legalną, porządną niszą. Wyższy zwrot wymaga: (a) timingu wejścia z
likwidacji, (b) selekcji aktywów, (c) ewentualnie overlayu kierunkowego (wyższe ryzyko).

UWAGA: pomija konwergencję basis i poślizg; funding to dominujący, ale nie jedyny
składnik. Pełny obraz da replay nagranych ticków (`run_backtest.py --data`).
"""
    with open(os.path.join(ROOT, "CARRY_VERDICT.md"), "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"\nZapisano werdykt -> CARRY_VERDICT.md")


if __name__ == "__main__":
    main()
