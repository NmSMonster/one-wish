# M3.5 — Walidacja edge (brama)

> Wynik uruchomienia `scripts/run_edge_validation.py`. To warunek przejścia do
> realnego handlu. **Nie usuwać — to świadectwo, że nie budujemy live na złudzeniu.**

## ⭐ AKTUALIZACJA: werdykt CARRY na realnym roku = GREEN (patrz CARRY_VERDICT.md)

Pierwotny FAIL niżej dotyczył strony **dyslokacji** na 32-tickowym wycinku (sekundy).
Główny edge tej strategii to jednak **carry** (funding co 8h), który ocenia się na
miesiącach/roku. Na **realnej ~rocznej historii funding Binance** (`scripts/study_funding.py`,
1200 rozliczeń / ~400 dni) carry delta-neutral jest **mocno dodatni po prowizjach**:

| aktywo | % dodatnich | brutto/rok | smoothed/rok (po fee) |
|---|---|---|---|
| BTC | 80.6% | +11.84% | +10.52% |
| ETH | 90.9% | +20.13% | +18.86% |
| SOL | 79.3% | +14.64% | +22.24% |
| XRP | 87.0% | +23.19% | +21.92% |

**Portfel delta-neutral ≈ +18%/rok po prowizjach.** Krótki wycinek (67 dni) trafił w
słaby reżim — dlatego rok ma znaczenie. Werdykt: **carry MA realny edge.**

Zastrzeżenia (uczciwie): model liczy sam funding (pomija konwergencję basis, poślizg,
jakość filli); ~18% to sufit „gross-ish", realne tarcia go obniżą. Przeszły funding ≠
przyszły (może się skompresować/odwrócić). Ryzyka: giełda, depeg USDT, margines nogi
perp, zmiana reżimu. Mimo to: to legalna, porządna nisza market-neutral.

## Metoda

Strumień ticków → prawdziwy `RepricingDetector` (fair value + model kosztów) →
liczymy, czy dyslokacje basis dają dodatni `expected_net_edge` PO KOSZTACH i jak
często. Werdykt: PASS / MARGINAL / FAIL.

## Wynik (pierwszy przebieg)

**Syntetyk (2000 ticków): PASS.** 101 sygnałów EDGE (5.05%), średni net edge przy
edge +11.5 bps. Potwierdza, że pipeline poprawnie wykrywa okazje, gdy istnieją.

**Realny Binance (snapshot 32 ticki): FAIL.**
- EDGE_DETECTED: 0
- net edge średnio: **−24.6 bps**
- mediana dyslokacji: **−4.53 bps** (perp poniżej fair — basis ujemny)
- funding: połowa aktywów ujemna (reżim v0 wymaga dodatniego)
- % ticków z net edge > 0: 0%

## Interpretacja

W bieżącym reżimie rynkowym naiwna strategia delta-neutral basis/funding **nie ma
przewagi po kosztach**. Główne przyczyny:
1. **Prowizje spot Binance (~0.1%)** → round-trip kosztów rzędu ~19–21 bps, którego
   mały basis nie przeskakuje.
2. **Basis ujemny + funding częściowo ujemny** akurat teraz (rynki cykliczne).

Zastrzeżenia: próbka 32 ticki z jednego momentu nie jest statystycznie pełna;
sandbox dokłada latencję (część „dane nieświeże"). Pełna brama wymaga realnego
okna wielogodzinnego/wielodniowego.

## Decyzja

- Infrastruktura (M6–M12) jest **agnostyczna względem strategii** — budujemy dalej.
- Basis/funding = strategia **v0 tylko PAPER**. **Live twardo zablokowany** do czasu
  pozytywnej walidacji na realnym, długim oknie danych.
- **Overlay kierunkowy (BTC→alty lead-lag)** to zaplanowane wyjście awaryjne —
  wpinamy w gotowy framework, jeśli basis/funding nie przejdzie realnej bramy.

## Aktualizacja: realny depth (po przeglądzie)

Pierwszy przebieg używał placeholdera głębokości ($500k). Po naprawie
`BinancePublicSource(fetch_depth=True)` pobiera realny orderbook. Okazało się, że
spotowa głębokość przy topie jest cienka (BTC ~$8k, ETH ~$12k przy 10 poziomach) —
placeholder mocno zawyżał jakość rynku. Dla mikro-nominałów slippage i tak jest
mały, więc dominującym kosztem pozostają prowizje, ale teraz model kosztów stoi na
prawdziwych danych, nie na zmyślonej płynności.

## Jak powtórzyć

Szybki podgląd (snapshot live + syntetyk):
```
python scripts/run_edge_validation.py
```

**Uczciwy werdykt (wymagany przed live)** — carry gra się przez 8h cykle funding,
więc potrzebny jest wielogodzinny/wielodniowy zapis realnych danych:
```
# 1) nagrywaj realne dane Binance przez dłuższy czas (Ctrl+C kończy):
python scripts/record_market.py --out data/binance_ticks.jsonl --cycles 0 --interval 2
# 2) puść je przez bramę edge:
python scripts/run_edge_validation.py --data data/binance_ticks.jsonl
```

Brama musi pokazać co najmniej MARGINAL na realnym, wielogodzinnym oknie
(obejmującym kilka rozliczeń funding), zanim ktokolwiek rozważy włączenie live (M12).
