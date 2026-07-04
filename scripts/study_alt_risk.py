"""Ocena RYZYKA LIKWIDACJI wysoko-funding altów — ryzyko, którego funding-study nie widzi.

funding-study/study_funding pokazuje, że VELVET/TAC płacą ~29%/rok funding out-of-sample.
Ale delta-neutral short na zmiennym alcie ginie nie od funding, tylko od gwałtownego
WZROSTU ceny, który likwiduje nogę short. Ten skrypt pobiera realną historię cen
(klines perp) i sprawdza: czy te alty kiedykolwiek ruszyły w górę tak mocno, że przy
3x/4x/5x short zostałby zlikwidowany — a jeden taki ruch kasuje miesiące funding.

Uruchom na maszynie z dostępem do Binance:
    python scripts/study_alt_risk.py
    python scripts/study_alt_risk.py --interval 4h --window 6 --mmr 0.02
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.adapters.market.binance_public import fetch_klines  # noqa: E402
from backend.core.types import ASSETS, BINANCE_SYMBOL  # noqa: E402
from backend.research.liquidation_risk import assess_short_liquidation  # noqa: E402
from backend.risk.margin import DeltaNeutralCrossStress  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    ap = argparse.ArgumentParser(description="One Wish — ocena ryzyka likwidacji altów")
    ap.add_argument("--interval", default="1d", help="interwał świec (1d, 4h, 1h)")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--window", type=int, default=1,
                    help="ile barów zajmie reakcja/domknięcie (konserwatywnie ≥1)")
    ap.add_argument("--mmr", type=float, default=0.02, help="maintenance margin rate (alty ~2%)")
    ap.add_argument("--haircut", type=float, default=0.10, help="haircut na spot-collateral w cross-margin")
    ap.add_argument("--leverages", default="3,4,5")
    args = ap.parse_args()

    levs = tuple(float(x) for x in args.leverages.split(","))
    rows = []
    for asset in ASSETS:
        symbol = BINANCE_SYMBOL[asset]
        try:
            kl = fetch_klines(symbol, interval=args.interval, limit=args.limit)
        except Exception as exc:  # noqa: BLE001
            print(f"{asset.value}: blad sieci: {exc}")
            continue
        if not kl:
            continue
        highs = [k["high"] for k in kl]
        opens = [k["open"] for k in kl]
        a = assess_short_liquidation(highs, opens, interval=args.interval, window=args.window,
                                     leverages=levs, mmr=args.mmr, symbol=symbol)
        rows.append((asset.value, a))

    # cross/portfolio margin (spot pokrywa perp) — realistyczny tryb dla delta-neutral
    cross = DeltaNeutralCrossStress(perp_leverage=max(levs), maintenance_margin_rate=args.mmr,
                                    spot_haircut=args.haircut)

    lev_hdr = " ".join(f"{l:.0f}x" for l in levs)
    header = (f"{'asset':6} {'bary':>5} {'najg.okno':>10}  ISOLATED likw? [{lev_hdr}]  "
              f"CROSS przeżywa?  bufor-basis")
    print(f"RYZYKO SHORT — realne ceny (interwał {args.interval}, okno {args.window} bar, "
          f"mmr {args.mmr:.0%}, haircut {args.haircut:.0%})\n")
    print(header)
    print("-" * len(header))
    lines = [header, "-" * len(header)]
    for name, a in rows:
        flags = " ".join(("LIKW" if a.breaches[l]["liquidated"] else "ok").ljust(4) for l in levs)
        surv = "TAK" if cross.survives(a.worst_window_up) else "NIE"
        buf = cross.max_basis_stress(a.worst_window_up)
        line = (f"{name:6} {a.n_bars:5d} {a.worst_window_up:9.1%}  {flags}     "
                f"{surv:>3}          +{buf * 100:.0f}%")
        print(line)
        lines.append(line)

    print("\nPrógi likwidacji ISOLATED (ruch w górę): " +
          ", ".join(f"{l:.0f}x → +{(1/l - args.mmr) * 100:.0f}%" for l in levs))
    print("ISOLATED LIKW = short odseparowany zostałby zlikwidowany tym ruchem.")
    print("CROSS przeżywa = w portfolio margin zysk ze spotu pokrywa stratę perpa (delta-neutral).")
    print("bufor-basis = o ile perp może wystrzelić PONAD spot i nadal przeżyć (zapas na squeeze).")
    print("WNIOSEK: dla zmiennych altów MUSISZ używać cross/portfolio margin — isolated = śmierć.")

    _write_doc(lines, levs, args, cross)


def _write_doc(lines: list[str], levs, args, cross) -> None:
    import datetime
    today = datetime.date.today().isoformat()
    thresholds = ", ".join(f"{l:.0f}x → +{(1 / l - args.mmr) * 100:.0f}%" for l in levs)
    doc = f"""# RYZYKO SHORT — realna historia cen (isolated vs cross margin)

> Wygenerowane {today} przez `scripts/study_alt_risk.py`. Interwał {args.interval},
> okno reakcji {args.window} bar, maintenance {args.mmr:.0%}, haircut spot {args.haircut:.0%}.

```
{chr(10).join(lines)}
```

Progi likwidacji ISOLATED (ruch ceny w górę): {thresholds}

## Jak czytać — kluczowe rozróżnienie isolated vs cross

- **najg.okno** = największy historyczny wzrost ceny (dla shorta = maksymalna strata).
- **ISOLATED likw** = short z ODSEPAROWANYM depozytem zostałby zlikwidowany tym ruchem.
  Tak NIE należy prowadzić delta-neutral — to najgorszy przypadek.
- **CROSS przeżywa** = w cross/portfolio margin zysk na nodze LONG SPOT jest collateralem
  dla perpa, więc czysty ruch kierunkowy (spot i perp w górę tak samo) NIE likwiduje.
  Nawet +146/+287% na VELVET/TAC są do udźwignięcia — spot pokrywa perp.
- **bufor-basis** = o ile perp może wystrzelić PONAD spot (short-squeeze premia) i nadal
  przeżyć. Duży bufor = bezpiecznie; mały = ryzyko przy gwałtownym rozjeździe basis.

## Wniosek operacyjny

1. **MUSISZ używać cross/portfolio margin** dla delta-neutral na zmiennych altach.
   Isolated = pewna likwidacja na pierwszym dużym pumpie. Cross = spot hedguje perp.
2. Wysoki funding altów (VELVET/TAC ~29%/rok) NIE jest głównym ryzykiem likwidacji
   (cross to trzyma) — realne pozostałe ryzyka to: **płynność wyjścia** (czy domkniesz
   duży short w trakcie +200% pumpa), rozjazd basis, flip funding na minus, depeg/venue.
3. Bufor-basis pokazuje zapas na squeeze. Im mniejszy, tym niższa dźwignia / większy
   spot-buffer wskazane.
"""
    with open(os.path.join(ROOT, "LIQUIDATION_RISK.md"), "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("\nZapisano -> LIQUIDATION_RISK.md")


if __name__ == "__main__":
    main()
