# CARRY — werdykt na realnej historii funding

> Wygenerowane 2026-06-30 przez `scripts/study_funding.py` na prawdziwej historii
> stawek funding Binance (endpoint fundingRate). round-trip fee = 18.6 bps.

```
asset    n  ~dni   sr/8h   %dod  brutto/rok   always  pos-only  smoothed
------------------------------------------------------------------------
BTC   1200   400  1.082b  80.6%      11.84%   11.67%     1.18%    10.52%
ETH   1200   400  1.838b  90.9%      20.13%   19.96%    13.28%    18.86%
SOL   1200   400  1.337b  79.3%      14.64%   14.47%    17.63%    22.24%
XRP   1200   400  2.117b  87.0%      23.19%   23.02%    13.93%    21.92%
```

PORTFEL BOTA (tylko dodatni funding: BTC, ETH, SOL, XRP): smoothed ~+18.39%/rok

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
