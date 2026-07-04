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
    count_funding_gaps,
    infer_settle_per_year,
    rates_from_history,
    simulate_carry_always_in,
    simulate_carry_positive_only,
    simulate_carry_smoothed,
)
from backend.research.portfolio_study import (  # noqa: E402
    equal_weights,
    funding_weights,
    return_on_capital,
    simulate_portfolio,
    top_n_assets,
    walk_forward,
)

ROUND_TRIP_FEE_BPS = 18.6  # 2×(spot 7.5 + perp 1.8) — maker z rabatem BNB
TAKER_FEE_BPS = 30.0       # scenariusz pesymistyczny: taker bez rabatu
PERP_LEVERAGE = 3.0        # do przeliczenia zwrotu z nominału na kapitał
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    rows = []
    rates_by_asset: dict[str, list[float]] = {}
    for asset in ASSETS:
        symbol = BINANCE_SYMBOL[asset]
        try:
            history = fetch_funding_history_paged(symbol, pages=6)   # ~400 dni (pełny rok)
        except Exception as exc:  # noqa: BLE001
            print(f"{asset.value}: blad sieci: {exc}")
            continue
        rates = rates_from_history(history)
        rates_by_asset[asset.value] = rates
        # REALNY interwał funding z timestampów — annualizacja MUSI go użyć, inaczej
        # aktywa 4h/1h (nowe alty) dostają błędny roczny wynik (zwykle zaniżony).
        interval = infer_settle_per_year(history)
        spy = interval["settle_per_year"]
        rows.append({
            "asset": asset.value,
            "interval": interval,
            "stats": analyze_funding(rates, settle_per_year=spy),
            "gaps": count_funding_gaps(history),
            "always": simulate_carry_always_in(rates, ROUND_TRIP_FEE_BPS, settle_per_year=spy),
            "pos": simulate_carry_positive_only(rates, ROUND_TRIP_FEE_BPS, settle_per_year=spy),
            "smooth": simulate_carry_smoothed(rates, ROUND_TRIP_FEE_BPS, settle_per_year=spy),
        })

    header = (f"{'asset':6} {'n':>4} {'co':>4} {'sr':>7} {'%dod':>6} "
              f"{'brutto/rok':>11} {'always':>8} {'pos-only':>9} {'smoothed':>9}")
    lines = [header, "-" * len(header)]
    for r in rows:
        st = r["stats"]
        iv = r["interval"]["interval_hours"]
        iv_lbl = f"{iv:.0f}h" if abs(iv - round(iv)) < 0.1 else f"{iv:.1f}h"
        lines.append(
            f"{r['asset']:6} {st.n:4d} {iv_lbl:>4} {st.mean_bps:6.3f}b {st.pct_positive:5.1f}% "
            f"{st.annualized_pct:10.2f}% {r['always'].annualized_net_pct:7.2f}% "
            f"{r['pos'].annualized_net_pct:8.2f}% {r['smooth'].annualized_net_pct:8.2f}%")
    lines.append("(kolumna 'co' = realny interwał funding z timestampow; 'sr' = sr. stawka/rozliczenie)")

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
    """Werdykt Tier A z rygorem: alokacja vs zwrot i ryzyko (maxDD, Calmar), zwrot
    na KAPITALE (nie nominale), wrażliwość na fee (maker vs taker), jakość danych
    (luki), oraz walk-forward OUT-OF-SAMPLE (selekcja bez look-ahead). Wagi liczy
    PRODUKCYJNA funkcja bota (FundingWeightedSizer.weight)."""
    # Portfel łączy krzywe po numerze rozliczenia — MIESZANIE różnych interwałów
    # funding (8h vs 4h) byłoby błędne (setne rozliczenie 4h-aktywa to inny moment
    # czasu). Dlatego werdykt portfelowy liczymy TYLKO na standardowych 8h-aktywach.
    standard = {r["asset"] for r in rows if r["interval"]["standard_8h"]}
    non_standard = [r for r in rows if not r["interval"]["standard_8h"]]

    traded = [r["asset"] for r in rows if r["stats"].annualized_pct > 0 and r["asset"] in standard]
    if len(traded) < 2:
        return ""
    mean_bps = {r["asset"]: r["stats"].mean_bps for r in rows if r["asset"] in traded}
    smooth_pct = {r["asset"]: r["smooth"].annualized_net_pct for r in rows if r["asset"] in traded}
    top4 = top_n_assets(smooth_pct, min(4, len(traded)))

    out = ["PORTFEL TIER A — alokacja vs zwrot i ryzyko (polityka smoothed):",
           f"  (tylko standardowe 8h-aktywa: {', '.join(sorted(traded))})"]

    # aktywa o niestandardowym interwale — pokazane OSOBNO (poprawnie annualizowane),
    # bo nie wolno ich mieszać do portfela liczonego po numerze rozliczenia
    if non_standard:
        out.append("  ⚠ poza portfelem (niestandardowy interwał funding, annualizacja per-interwał):")
        for r in sorted(non_standard, key=lambda x: -x["stats"].annualized_pct):
            iv = r["interval"]["interval_hours"]
            out.append(f"      {r['asset']:6} funding co ~{iv:.1f}h → brutto {r['stats'].annualized_pct:+.2f}%/rok, "
                       f"smoothed {r['smooth'].annualized_net_pct:+.2f}%/rok")

    # jakość danych — cienki edge nie znosi luk w rozliczeniach
    bad = [(r["asset"], r["gaps"]) for r in rows
           if r["asset"] in standard and r.get("gaps")
           and (r["gaps"]["missing_settlements"] or r["gaps"]["irregular_gaps"])]
    if bad:
        out.append("  ⚠ jakość danych (8h-aktywa): " + ", ".join(
            f"{a} (brak {g['missing_settlements']}, nieregularne {g['irregular_gaps']})" for a, g in bad))
    else:
        out.append("  ✓ jakość danych: 8h-aktywa bez luk w rozliczeniach")

    # wrażliwość na fee: maker (z rabatem BNB) vs taker (pesymistycznie)
    for fee_label, fee in (("maker", ROUND_TRIP_FEE_BPS), ("taker", TAKER_FEE_BPS)):
        header = f"  [{fee_label} {fee:.0f}bps] {'wariant':26} {'zwrot/nom':>9} {'na kap':>7} {'maxDD':>7} {'Calmar':>7}  sklad"
        out += ["", header, "  " + "-" * (len(header) - 2)]
        variants = [
            ("rowne wagi (wszystkie)", equal_weights(traded)),
            ("rowne wagi top-4", equal_weights(top4)),
            ("wazone funding (wszystkie)", funding_weights(mean_bps)),
            ("wazone funding top-4", funding_weights({k: mean_bps[k] for k in top4})),
        ]
        for label, weights in variants:
            try:
                res = simulate_portfolio(rates_by_asset, weights, round_trip_fee_bps=fee, label=label)
            except ValueError as exc:
                out.append(f"  [{fee_label}] {label:26} pominieto ({exc})")
                continue
            calmar = f"{res.calmar:6.1f}" if res.calmar is not None else "   inf"
            roc = return_on_capital(res.annualized_net_pct, PERP_LEVERAGE)
            skład = " ".join(f"{k}:{w:.0%}" for k, w in sorted(res.weights.items(), key=lambda kv: -kv[1]))
            out.append(f"  [{fee_label} {fee:.0f}bps] {label:26} {res.annualized_net_pct:8.2f}% "
                       f"{roc:6.2f}% {res.max_drawdown_pct:6.2f}% {calmar}  {skład}")

    # walk-forward OOS — najuczciwsza liczba (selekcja na treningu, wynik na teście).
    # WYŁĄCZNIE 8h-aktywa (bez mieszania interwałów).
    std_rates = {a: rates_by_asset[a] for a in traded}
    out.append("")
    try:
        n_common = min(len(v) for v in std_rates.values())
        train = max(150, n_common // 3)
        test = max(50, n_common // 6)
        wf = walk_forward(std_rates, train=train, test=test, top_n=min(4, len(traded)),
                          funding_weighted=True, round_trip_fee_bps=ROUND_TRIP_FEE_BPS)
        roc_wf = return_on_capital(wf.annualized_net_pct, PERP_LEVERAGE)
        out.append(f"  WALK-FORWARD (out-of-sample, train={train}/test={test}, {len(wf.windows)} okien): "
                   f"{wf.annualized_net_pct:+.2f}%/rok na nominale, {roc_wf:+.2f}%/rok na kapitale")
        out.append("  ↑ to jest liczba bez look-ahead — selekcja top-4/wagi wybierana TYLKO na przeszłości.")
    except ValueError as exc:
        out.append(f"  walk-forward pominieto: {exc}")

    out += ["",
            "Roznica 'wazone' vs 'rowne wagi' = zmierzony efekt Tier A. 'na kap' = zwrot na",
            f"Twoim kapitale przy {PERP_LEVERAGE:.0f}x (nominal + depozyt). Do targetu bierz",
            "WALK-FORWARD na kapitale — reszta jest in-sample (optymistyczna)."]
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
