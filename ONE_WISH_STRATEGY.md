# ONE WISH — Strategia (M1)

> Dokument taktyki. Definiuje **czym** One Wish handluje i **jakiej przewagi** szuka.
> Na tym etapie nie ma kodu tradingowego — to precyzyjna specyfikacja zasad:
> kiedy bot może wejść, kiedy ma czekać, kiedy ma odrzucić sygnał, kiedy ma uciekać.

---

## 0. Kontekst i decyzje

- **Stack:** Python (silnik bota) + istniejące GUI (HTML/JS/canvas) podpięte przez warstwę danych (WebSocket/REST).
- **Rynek:** **Binance** — rynek **spot** + **USDT-M perpetual futures**. Rdzeń systemu jest venue-agnostyczny (za interfejsem `Market Data Adapter` / `Exchange Adapter`); Kraken/Bybit pozostają jako zapas.
- **Dlaczego nie Polymarket / opcje binarne:** produkt binarny UP/DOWN „settle do $1" nie jest legalnie dostępny dla klienta detalicznego w Polsce (zakaz ESMA na opcje binarne dla detalu w UE; rynki predykcyjne typu Polymarket działają w szarej strefie). Dlatego ideę **„opóźnionej wyceny"** przenosimy na produkt, który istnieje legalnie i jest dostępny z Polski.

---

## 1. Filozofia przewagi: „opóźniona wycena" w wersji spot–perp

Oryginalna idea: jeden rynek **spóźnia się z wyceną** względem drugiego, a my łapiemy to opóźnienie zanim się domknie.

Na Binance przekładamy to na relację **spot ↔ perpetual**:

- **Spot prowadzi wycenę.** Gdy aktywo bazowe (BTC/ETH/SOL/XRP) robi nagły ruch, cena spot reaguje pierwsza.
- **Perp laguje przez bazę (basis).** Cena perpetuala chwilowo odchyla się od „uczciwej" relacji do spotu — basis (różnica perp − spot) wychodzi poza poziom uzasadniony oczekiwanym fundingiem.
- **Konwergencja.** Basis wraca do uczciwego poziomu, a perpetual rozlicza **funding** (na Binance co 8 h: 00:00 / 08:00 / 16:00 UTC).

Łapiemy dwie nakładające się rzeczy:

1. **Dyslokacja basis (rdzeń „opóźnionej wyceny"):** wejście, gdy basis odchyli się od wartości uczciwej po ruchu spotu; wyjście na konwergencji.
2. **Carry / funding harvest (nośność):** w trakcie trzymania pozycji zbieramy funding po stronie, która jest opłacana.

**Kluczowa właściwość: delta-neutral.** Trzymamy jednocześnie **long spot + short perp** w równej wielkości. Ekspozycja kierunkowa na cenę ≈ 0 — nie zakładamy się, czy aktywo pójdzie w górę czy w dół. Zarabiamy na **relacji** spot↔perp i na funding, nie na kierunku. To świadomy wybór: bezpieczniejszy fundament na pierwszego bota z realnym kapitałem.

> **Brak dźwigni kierunkowej.** Noga long spot 1× jest w pełni pokryta gotówką; noga short perp 1× jest zabezpieczona jako hedge (nie spekulacja na spadek). Margines na perp utrzymujemy z buforem, żeby nigdy nie zbliżyć się do likwidacji.

---

## 2. Zakres handlu

| Element | Wartość v0 (do kalibracji w M3.5/M10) |
|---|---|
| Instrumenty | BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT (spot + USDT-M perp) |
| Struktura pozycji | long spot + short perp (równa wielkość, delta ≈ 0) |
| Reżim v0 | **tylko funding dodatni** (longs płacą shorts → short perp dostaje funding) |
| Okna obserwacji sygnału | 5 min / 15 min / 1 h (momentum + zmienność spotu) |
| Cykl funding | co 8 h (Binance) |
| Horyzont trzymania | od pojedynczej dyslokacji do najbliższego rozliczenia funding |

**Świadome ograniczenie v0:** wchodzimy wyłącznie w reżimie **funding dodatni** (long spot + short perp). Reżim funding ujemny wymagałby shortowania spotu / pożyczki — to złożoność i ryzyko, których w v0 nie bierzemy. Funding ujemny ⇒ stan **NO_TRADE**, nie odwracamy strategii.

---

## 3. Sygnały wejścia (model danych)

Wejścia liczone z następujących wejść (formalizacja w M4 `FairValueModel`, M5 `RepricingDetector`):

- `spot_mid`, `perp_mid`, `index_price` (Binance index)
- `funding_rate` bieżący + `predicted_funding` do najbliższego rozliczenia
- `basis_bps = (perp_mid − index_price) / index_price × 10000`
- `fair_basis_bps` — model wartości uczciwej (oczekiwany carry funding do rozliczenia + koszt nośności)
- `basis_dislocation = basis_bps − fair_basis_bps`
- `spot_momentum` (5m/15m/1h), `realized_vol`
- głębokość orderbooka i spread na obu nogach (spot + perp)
- `data_lag_ms` (świeżość strumieni)

---

## 4. Sytuacja WEJŚCIA (kiedy bot może wejść)

Wejście **LONG-CARRY** (long spot + short perp) tylko gdy **wszystkie** warunki spełnione jednocześnie:

1. **Funding sprzyja:** `funding_rate > 0` i `predicted_funding > 0` (short perp będzie opłacany).
2. **Dyslokacja basis:** `basis_dislocation ≥ próg_wejścia` (perp drożeje względem uczciwej relacji po ruchu spotu).
3. **Edge netto dodatni:** `expected_net_edge ≥ min_edge` (definicja w §7 — po WSZYSTKICH kosztach).
4. **Płynność OK:** spread na obu nogach ≤ `max_spread`, głębokość ≥ `min_depth` dla zamierzonego rozmiaru.
5. **Dane świeże:** `data_lag_ms ≤ max_lag` na wszystkich strumieniach.
6. **Czas do rozliczenia:** do najbliższego funding zostało ≥ `min_time_to_settle` (żeby carry zdążył się zmaterializować) — lub setup czysto dyslokacyjny z jasnym targetem konwergencji.
7. **Limity ryzyka:** Risk Manager (M6) zwraca `RISK_APPROVED` (ekspozycja na aktywo, liczba pozycji, limit dzienny itd. nieprzekroczone).

Wielkość pozycji: stała frakcja kapitału na pozycję (v0), równa na obu nogach, w granicach limitów Risk Managera.

---

## 5. Sytuacja BRAKU WEJŚCIA (kiedy bot ma czekać / odrzucić sygnał)

Stan **NO_TRADE** (event `NO_TRADE_CONDITION`), gdy zachodzi cokolwiek z poniższych:

- `funding_rate ≤ 0` lub bliski zera (reżim nieobsługiwany w v0),
- `basis_dislocation < próg_wejścia` (brak realnej dyslokacji),
- `expected_net_edge < min_edge` (przewaga znika po kosztach) — **to najczęstszy i najważniejszy filtr**,
- spread za szeroki lub płynność za mała,
- `data_lag_ms > max_lag` (nie ufamy nieświeżym danym),
- za blisko rozliczenia funding bez jasnego targetu konwergencji,
- przekroczony limit ekspozycji / liczby pozycji (Risk Manager mówi „NIE"),
- otwarta równoważna pozycja na tym samym aktywie (brak dublowania).

Zasada nadrzędna: **brak edge to nie strata — to po prostu brak transakcji.** Bot domyślnie czeka.

---

## 6. Sytuacja AWARYJNA (kiedy bot ma uciekać)

Eventy awaryjne → **kill switch** i/lub natychmiastowe domknięcie obu nóg (`FLATTEN`):

- błędy API / odrzucenia zleceń powyżej progu, brak połączenia z giełdą,
- strumień danych „zamrożony" > `stale_feed_s`,
- **rozjazd basis** poza `basis_stop` (dyslokacja pogłębia się zamiast konwergować),
- współczynnik marginesu na nodze short perp zbliża się do strefy likwidacji (`margin_ratio` < bufor),
- **depeg USDT** lub anomalia index price,
- przekroczony **dzienny limit straty** → twardy stop na resztę dnia,
- ręczny kill switch operatora.

Reguła: w razie wątpliwości **domykamy obie nogi naraz** (utrzymanie delta-neutralności w trakcie wyjścia) i zatrzymujemy wejścia.

---

## 7. Model kosztów i definicja edge (jawny, od początku)

Przewagę liczymy **netto** już na etapie detekcji (M5) — nie dopiero w backteście. Sygnał bez dodatniego edge po kosztach to fałszywy alarm.

```
expected_net_edge =
      expected_basis_convergence            # zysk z domknięcia dyslokacji
    + expected_funding_accrued              # funding zebrany w trakcie trzymania
    − fees_round_trip_spot                  # prowizje spot (wejście + wyjście)
    − fees_round_trip_perp                  # prowizje perp (wejście + wyjście)
    − expected_slippage_both_legs           # poślizg z głębokości orderbooka
    − spread_cost
```

Założenia kosztowe v0 (do potwierdzenia realnymi danymi w M3/M3.5):
- prowizja taker perp ≈ 0,04%, spot ≈ 0,1% (maker niżej — preferujemy zlecenia limit),
- round-trip = wejście + wyjście na obu nogach,
- poślizg estymowany z realnej głębokości orderbooka dla rozmiaru pozycji.

`min_edge` ustawiamy z **zapasem** ponad zero (margines błędu modelu i kosztów). Konkretna wartość — do kalibracji w M3.5/M10.

---

## 8. Nazwane ryzyka

- **Latencja = cała przewaga.** Dyslokacja basis może zniknąć, zanim zlecenie się wypełni. Mitygacja: zlecenia maker/limit z jasnym progiem, pomiar realnej latencji fill od M3, rezygnacja z wejścia przy nieświeżych danych. Realne pytanie „czy w ogóle jesteśmy dość szybcy" rozstrzyga M3.5/M11.
- **Rozjazd basis (basis blowout):** basis potrafi pogłębić dyslokację przed konwergencją. Delta-neutralność chroni przed kierunkiem ceny, ale nie przed zmiennością samego basis → twardy `basis_stop`.
- **Likwidacja nogi short perp:** zarządzana buforem marginesu; zysk na spocie kompensuje stratę na perp (delta ≈ 0), ale margines pilnujemy osobno.
- **Flip funding:** dodatni funding może się odwrócić → przegląd warunku trzymania, wyjście gdy nośność znika.
- **Depeg USDT / anomalia oracle:** stan awaryjny, flatten.
- **Awaria / downtime giełdy, reconnect, niepełne fille:** obsługa w Execution/Order Manager (reconcyliacja stanu po restarcie, idempotencja zleceń — `client_order_id`).
- **Konkurencja botów:** carry/basis jest grane przez innych; dlatego liczymy edge netto z zapasem i walidujemy realność w M3.5.

---

## 9. Warunek przejścia dalej — brama M3.5 (walidacja edge)

Zanim zbudujemy M4–M9, w **M3.5** na realnych danych Binance sprawdzamy:

- czy dyslokacje basis po ruchach spotu **faktycznie występują** z mierzalną częstotliwością,
- czy **przeżywają koszty** (fee + poślizg + spread) — tzn. `expected_net_edge` bywa dodatni z zapasem,
- czy okno na wejście jest realne wobec naszej latencji.

**Jeśli edge nie istnieje po kosztach — zatrzymujemy się tutaj** i zmieniamy taktykę (np. overlay kierunkowy lead-lag BTC→alty) zamiast budować całą resztę na fałszywym założeniu.

---

## 10. Efekt M1 / status

- ✅ Zdefiniowana taktyka: „opóźniona wycena" jako konwergencja basis spot↔perp + carry funding, delta-neutral.
- ✅ Jasne zasady: wejście (§4), brak wejścia (§5), awaria (§6).
- ✅ Jawny model kosztów i definicja edge netto (§7) — od początku, nie w backteście.
- ✅ Nazwane ryzyka, latencja jako ryzyko #1 (§8).
- ✅ Brama walidacji edge M3.5 jako warunek dalszej budowy (§9).
- ⛔ Brak kodu tradingowego. Brak realnych pieniędzy. To wyłącznie specyfikacja taktyki.

**Następny krok:** M2 — `ARCHITECTURE.md` (szkielet modułów, przepływ danych, struktura katalogów), bez implementacji realnego handlu.

---

## 11. Implementacja edge: carry-hold (kluczowa korekta po budowie)

Pierwsza wersja silnika churnowała: wchodziła i wychodziła na każdej dyslokacji,
płacąc pełny round-trip prowizji i **nigdy nie inkasując funding**. To było błędne
— funding carry to główny, strukturalny edge tej strategii.

Poprawiona logika (tryb **carry**, domyślny):
- **Wejście** na korzystnej dyslokacji (lepsza cena wejścia: perp drogi → dobry dla short).
- **Trzymanie** pary delta-neutral przez kolejne rozliczenia funding — short perp
  inkasuje funding co cykl (8h). Zanik dyslokacji NIE jest powodem do wyjścia.
- **Wyjście** dopiero, gdy nośność znika (`predicted_funding ≤ próg`) albo basis
  odwróci się poza stop (`observed_basis ≤ basis_stop`).

Naliczanie funding modeluje `FundingAccrual`: przy przekroczeniu rozliczenia
short perp dostaje `-perp_qty × funding_rate × perp` (dodatnie przy funding > 0).

Dowód mechanizmu (backtest syntetyczny, funding co 120s): scalp = 134 wejścia,
$0 funding, −$8.20 netto (prowizje go zabijają); **carry = 4 wejścia, +$2.51
funding, +$1.76 netto.** To potwierdza poprawność *mechanizmu* — NIE jest tezą o
realnym zysku (syntetyk ma stały dodatni funding). Realny werdykt: §9 + recorder
(`scripts/record_market.py` → `run_edge_validation.py --data`), na wielogodzinnym
zapisie obejmującym prawdziwe cykle funding.
