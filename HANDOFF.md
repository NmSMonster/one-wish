# ONE WISH — Handoff dla sesji Claude Code (stan + plan)

> Czytasz to jako świeża sesja bez wcześniejszej pamięci. Ten plik jest
> samowystarczalnym źródłem prawdy. Właściciel (Borys) jest na wyjeździe i pisze
> **z telefonu** — krótkie wiadomości. Bądź konkretny, działaj samodzielnie, nie
> zalewaj tekstem. Język rozmowy: polski.

## TL;DR

One Wish to **realny bot tradingowy**: delta-neutral **basis/funding carry** na
Binance (long spot + short perp, inkasowanie funding). Backend Python, event-driven,
**360 testów pytest zielonych**. Edge (carry) **zwalidowany na ~roku realnej historii
funding (~+18%/rok delta-neutral po prowizjach)**. Bot poprawnie wchodzi w carry na
realnych danych. Realny handel jest **domyślnie zablokowany** — jesteśmy w fazie
paper/walidacji, przed transportem na testnecie i pilotem.

Folder roboczy: katalog repo (na GitHubie). Testy: `python -m pytest -q`.

## 1. Czym jest i jak zarabia

- **Strategia carry:** trzymasz parę delta-neutral (long spot + short perp w równym
  nominale → zero ekspozycji kierunkowej). Short perp **inkasuje funding** co 8h
  (00:00/08:00/16:00 UTC), gdy funding dodatni. To jest edge — stały dochód, nie
  zakład o kierunek.
- **Wejście (tryb „carry", domyślny):** wchodzimy, gdy forward funding > próg i
  basis nie odwrócony i rynek płynny. NIE wymagamy dyslokacji.
- **Wyjście:** gdy WYGŁADZONY (EMA) funding spada poniżej progu (reżim się odwraca)
  albo basis łamie stop. NIE churnujemy na pojedynczym ujemnym ticku (to było paliło
  prowizje — naprawione).
- **Tryb „dislocation"** (opcjonalny, do badań): wejście na perp-rich spike.

## 2. Status — co działa (360 testów zielonych)

Pełny pipeline event-driven (`backend/`):

```
MarketData (Binance read-only + synthetic + replay)
  → FairValue → RepricingDetector (carry/dislocation) → StrategyPolicy (carry-hold, EMA exit)
  → RiskManager (limity + weto + kill + circuit breaker) → ExecutionEngine
  → OrderManager (maszyna stanów, invariant „delta-neutral albo flat")
  → PaperBroker → PositionBook (PnL + funding accrual)
  → EventBus → {Database/audit (sqlite), GUI API (WebSocket), Monitoring}
```

Bezpieczeństwo: **MarginWatchdog** (kontrolowany flatten short-perp przed
likwidacją), **CircuitBreaker** (anomalia premii/OI/głębokość/likwidacje),
**OrderManager** (retry, idempotentny coid, kompensacja niepełnej pary).

Dane: recorder (forward funding z mark+premia, OI, historia funding, realny depth),
strumień likwidacji (WS). Selekcja aktywów: `scan_universe.py`.

GUI: osobny statyczny cockpit (`gui/` lub pliki w root: index.html/js/...), łączy
się przez WebSocket `ws://127.0.0.1:8765/gui`. Robi go **Codex**. Działa w trybie
mock i ws.

## 3. Werdykt edge — GREEN

`scripts/study_funding.py` policzył carry na realnej ~rocznej historii funding
Binance (1200 rozliczeń/~400 dni): BTC +10.5%, ETH +18.9%, SOL +22.2%, XRP +21.9%/rok
(smoothed, po prowizjach), 80–91% rozliczeń dodatnich. Portfel ~+18%/rok. → patrz
`CARRY_VERDICT.md`. **To jest empiryczny edge na realnych danych.**

Zastrzeżenia (uczciwie): model funding-only (pomija konwergencję basis, poślizg);
~18% to sufit, realne tarcia obniżą; przeszły funding ≠ przyszły; ryzyka:
giełda/depeg/margines/zmiana reżimu. Mimo to: legalna, porządna nisza market-neutral.

## 4. ⏸️ ZAPARKOWANE (czeka na powrót właściciela do PC)

Borys nagrał **11,5 h realnych danych** (`data/binance_ticks.jsonl`, 11508 ticków),
przechodzących przez 2 rozliczenia funding (00:00 i 08:00 UTC). Plik jest **tylko na
jego PC**, niedostępny na wyjeździe. Gdy wróci, jedna komenda da pierwszy pełny
werdykt carry na realnych ticakch (z funding + basis + poślizg):

```
python scripts/run_backtest.py --data data/binance_ticks.jsonl
```

Spodziewamy się: wejścia (carry na dodatni-funding aktywach) + realnie zainkasowany
funding przez 2 rozliczenia + rzeczywisty PnL. **Nie ruszaj tego bez pliku — nie ma
go w repo.** (Wcześniejsze 0 wejść było bugiem wejścia — już naprawione.)

## 5. Architektura — mapa modułów

```
backend/
  core/        events, types, bus (EventBus), clock, serialize
  adapters/
    market/    binance_public (REST read-only + forward funding + OI + depth),
               synthetic, replay, liquidations (WS), base (MarketDataAdapter)
    exchange/  paper (PaperBroker), binance_live (transport testnet, domyślnie OFF)
  model/       fair_value, costs
  signal/      repricing (RepricingDetector: entry_mode carry/dislocation)
  strategy/    policy (StrategyPolicy: carry-hold + EMA funding exit)
  risk/        manager (RiskManager + RiskConfig), margin (MarginModel+Watchdog),
               circuit_breaker (CircuitBreaker)
  execution/   book (PositionBook+PnL), order_manager (maszyna stanów+invariant),
               engine (ExecutionEngine, cienka warstwa nad OM), funding (accrual),
               quantize (filtry symbolu+kwantyzacja exchangeInfo)
  storage/     db (sqlite), audit (odtwarzanie decyzji)
  api/         gui_ws (serwer WebSocket, kontrakt GUI)
  app/         pipeline, runner (OneWishApp), report, budget (kapitał paper-live)
  backtest/    engine (Backtester), metrics
  research/    edge_validation (M3.5), funding_study (werdykt carry),
               portfolio_study (walidator Tier A: wagi vs zwrot/ryzyko),
               universe (ranking aktywów), recorder (zapis ticków),
               liquidation_risk (ryzyko likwidacji altów z cen)
scripts/       study_funding, run_backtest, run_edge_validation, run_paper_live,
               record_market, record_liquidations, scan_universe, run_testnet_smoke
tests/         pełna suita pytest (360)
Dokumenty:     README, ONE_WISH_STRATEGY.md, ARCHITECTURE.md, EDGE_VALIDATION.md,
               CARRY_VERDICT.md, UNIVERSE_SCAN.md, DATA_CONTRACT.md, RUNBOOK.md, ten HANDOFF.md
```

## 6. Jak uruchomić

```
python -m pytest -q                              # 360 testów
python scripts/study_funding.py                  # werdykt carry na historii funding (natychmiast)
python scripts/scan_universe.py --top 25         # ranking aktywów po carry
python scripts/run_backtest.py                   # backtest carry vs scalp (synthetic)
python scripts/run_backtest.py --data PLIK.jsonl # backtest na nagranych realnych ticakch
python scripts/run_paper_live.py --mode synthetic --watch   # runner + GUI live
python scripts/record_market.py --out data/x.jsonl --cycles 0 --interval 2  # nagrywanie (READ-ONLY)
```
Zależności: `pip install -r requirements.txt` (websockets, pyyaml, pytest).

## 7. Plan / backlog (priorytetowo)

Z przeglądów Codexa „survive live" — zrobione: P0 OrderManager, #4 kwantyzacja,
#6 circuit breakers, + krytyczny fix wejścia carry.

**Zrobione w sesji handoff (branch claude/handoff-documentation-tgert9):**
- ✅ **Optymalizacja recordera** — ceny co cykl; OI/głębokość co `slow_every` cykli
  z cache + równoległe pobieranie (ThreadPool) + stale-on-error. Recorder:
  `--slow-every` (domyślnie 15). `slow_every=1` = stare zachowanie (zero regresji).
- ✅ **#5 collateral/margin stress** — `MarginStressTester`: zdrowie nogi short-perp
  pod ruchem +10/20/30%, per-symbol maintenance brackets (`MarginModel.mmr`),
  raport per pozycja + summary (likwidacja/flatten/safe). Model DWÓCH portfeli
  (`TwoWalletLedger`/`wallet_split` w budget.py): spot-wallet vs futures-wallet —
  para wymaga środków w OBU (long=gotówka spot, short=depozyt futures), `can_open`
  pilnuje obu osobno.
- ✅ **#7 chaos-testy** — martwa noga (perp nie domyka), mieszany chaos
  spot-partial+perp-dead, restart między nogami + recovery do flat, flatten-noop,
  stale feed mid-sequence → EMERGENCY_STOP → flatten (E2E), DB write fail izolowany
  (awaria zapisu nie zabija szyny).
- ✅ **#8 alerty poza GUI + runbook** — `AlertManager` (backend/monitoring/alerts.py):
  pluggable sinki (Log/Buffer/Webhook), próg severity, throttling per (rodzaj,aktywo),
  CRITICAL nigdy nietłumiony; konfiguracja z env (Discord/Telegram), sekrety tylko
  z env. Wpięte do runnera. Reaguje na margin/kill/emergency/circuit/stale/lag/reject.
  Runbook operatora: `RUNBOOK.md`.
- ✅ **#2 cost telemetry/shadow** — `CostTelemetry` (backend/monitoring/telemetry.py):
  mierzy REALNY poślizg egzekucji per noga (fill vs referencja po coid), realne fee
  bps, realny spread (spot/perp) i opóźnienie danych — z fillów i ticków. Shadow
  (obserwacja, bez wpływu na decyzje). Wpięte do runnera (snapshot w logu na koniec).
  Pamięć referencji ograniczona (FIFO). Pozwala weryfikować założenia `CostModel`.
- ✅ **Transport na TESTNECIE** — `BinanceLiveAdapter` ma realny podpisany transport
  (HMAC-SHA256), zlecenia MARKET spot + USDT-M perp, parsowanie fillów (spot fills /
  futures avgPrice), `reconcile()` (otwarte zlecenia spot+fut), `account_state()`.
  Domyślnie nadal ZABLOKOWANY: wysyłka wymaga live_enabled + klucze z env + arm(token)
  + `transport_implemented=True` + `testnet=True` (mainnet osobno za `allow_mainnet`).
  Limit nominału przed transportem. Smoke-test: `scripts/run_testnet_smoke.py --yes`
  (wymaga kluczy TESTNET z env). Jedyny styk I/O (`_signed_request`) izolowany i
  przetestowany przez stub (zero sieci w testach). Lost-ack nadal TODO (reconcile-
  before-retry) przed realnym pilotem.
- ✅ **Rozszerzone uniwersum (backend)** — DOGE/ZEC/VELVET/TAC/HYPE dodane do
  `Asset` enum + `BINANCE_SYMBOL` + `ASSETS` (kolejność deterministyczna), brackety
  maintenance (margin.py: alty konserwatywnie wyżej) i ceny demo (synthetic). Test
  spójności pilnuje, że każde aktywo ma symbol/bracket/cenę. **TODO (Codex):** lista
  aktywów + kolory w GUI. UWAGA sizing: perp minNotional BTC $50 / ETH $20 — przy
  małym budżecie celuj w tańsze alty (DOGE/XRP).
- ✅ **Lost-ack handling (reconcile-before-retry)** — `OrderResult.uncertain` (ack
  zgubiony ≠ czysta odmowa), `ExchangeAdapter.query_order(coid)` (None=niezłożone /
  OrderResult=istnieje / wyjątek=nieustalony), live adapter: GET order po
  origClientOrderId (wykrywa -2013), transport-error → uncertain. OrderManager przy
  uncertain pyta giełdę o stan TEGO coid przed ponowieniem: stosuje istniejące fille
  (zero dubla), niezłożone → bezpieczne ponowienie, nieustalony stan → przerywa i
  kompensuje do flat. Pełne pokrycie testami (3 scenariusze OM + query_order).
- ✅ **#3 funding reconciliation** — `FundingReconciler` (backend/monitoring/reconcile.py):
  sumuje model funding z eventów FUNDING_ACCRUED per aktywo, agreguje realny income
  (`aggregate_income` z rekordów FUNDING_FEE), porównuje (model/real/diff + totale) i
  flaguje rozbieżność po DWÓCH progach (absolutnym i względnym). Adapter:
  `funding_income()` (GET /fapi/v1/income FUNDING_FEE). Pełne pokrycie testami.
- ✅ **Tier A #1: sizing ważony forward funding** (`backend/strategy/sizing.py`,
  `FundingWeightedSizer`) — cel: podnieść zwrot 18%→~25-30% BEZ zmiany charakteru
  strategii (wciąż market-neutral, Binance-only), przesuwając kapitał w stronę
  wyżej płacących aktywów zamiast płaskiego nominału na każdą parę (BTC 10.5%/rok
  dostaje mniej, DOGE 27.7%/rok więcej — UNIVERSE_SCAN.md). Opt-in: `funding_weighted=
  True` w `OneWishApp`/`Pipeline` (domyślnie WYŁĄCZONE, zero zmiany zachowania) albo
  `--funding-weighted` w `run_paper_live.py`. Sizer wpięty i do `StrategyPolicy`
  (realny nominał zlecenia), i do `RepricingDetector` (depth-check + koszt na TYM
  SAMYM efektywnym nominale — bez tego większa ważona pozycja przechodziłaby próg
  płynności policzony dla mniejszego płaskiego nominału; naprawione i pokryte testem
  wykazującym różnicę). **WAŻNE — nie zwalidowane liczbowo:** to jest mechanizm, nie
  zmierzony wynik. Żeby uczciwie zapisać nowy % w CARRY_VERDICT.md, trzeba odpalić
  `study_funding.py` w wariancie ważonym na realnych danych (wymaga dostępu do
  Binance, zablokowanego w tym sandboxie) i dopiero wtedy zaktualizować liczbę.
  Rozmowa z właścicielem o dalszych tierach: Tier B (multi-venue, 40-70%/rok,
  wyższe ryzyko venue/counterparty) świadomie odłożony jako faza 2, PO zwalidowaniu
  Tier A na realnych danych — enum `Venue` (core/types.py) to nieużywany jeszcze stub
  pod tę fazę.
- ✅ **Tier A #2: walidator portfelowy** (`backend/research/portfolio_study.py`) —
  narzędzie, które zamienia obietnicę Tier A w POMIAR: porównuje na historii funding
  równe wagi vs top-N vs ważenie funding, z metrykami ryzyka (max drawdown, Calmar,
  najgorsze rozliczenie) z krzywej kapitału per rozliczenie. Kluczowa uczciwość:
  wagi liczy PRODUKCYJNA funkcja bota (`FundingWeightedSizer.weight`) — badanie i
  egzekucja nie mogą się rozjechać; polityka per aktywo = smoothed z parytetem co do
  bps z `simulate_carry_smoothed` (test); historie wyrównane od ogona. Wpięte w
  `scripts/study_funding.py` — sekcja „PORTFEL TIER A" w wydruku i CARRY_VERDICT.md.
  **Jak dostać realny werdykt Tier A:** `python scripts/study_funding.py` na maszynie
  z dostępem do Binance → tabela wariantów z zwrot/maxDD/Calmar; dopiero TE liczby
  wpisać jako nowy target. (Smoke offline na profilach z UNIVERSE_SCAN: równe ~17.6%
  → ważone top-4 ~20.9% — ilustracja działania narzędzia, NIE werdykt.)
- ✅ **Tier A #3: rygor walidatora (anty-look-ahead + realizm)** — przed spaleniem
  „jednego strzału" na realnych danych dorzucone cztery rzeczy, które zamieniają
  optymistyczną liczbę w uczciwą:
  1. **Walk-forward OUT-OF-SAMPLE** (`walk_forward`): selekcja top-N i wagi wybierane
     WYŁĄCZNIE na oknie treningowym, wynik mierzony na następnym — eliminuje
     look-ahead bias („wybraliśmy zwycięzców z perspektywy czasu"). Konserwatywnie:
     każde okno rusza na zimno (świeże fee); okna bez dodatniego funding nie handlują.
  2. **Zwrot na KAPITALE** (`return_on_capital`): ~18% liczone jest od nominału pary,
     a kapitał = nominał + depozyt perp → przy 3x realny zwrot ×0.75. Werdykt pokazuje
     obie kolumny (nominał / kapitał).
  3. **Wrażliwość na fee**: cała tabela liczona dla maker (18.6 bps) I taker (30 bps).
  4. **Jakość danych** (`count_funding_gaps`): detekcja brakujących/nieregularnych
     rozliczeń w historii + `pages=4→6` (~400 dni jak w UNIVERSE_SCAN).
  Do targetu bierz liczbę **WALK-FORWARD na kapitale** — reszta jest in-sample
  (optymistyczna). Smoke offline (profile UNIVERSE_SCAN): nagłówkowe ~21% na nominale
  in-sample → **~15.6% na kapitale out-of-sample** — to jest uczciwy rząd wielkości.
- ✅ **Tier A #4: fix interwału funding (na podstawie REALNEGO werdyktu 2026-07-04)** —
  właściciel odpalił `study_funding.py` na realnych danych. **Kluczowe odkrycia:**
  (a) funding OSTYGŁ — 8h-majorsy (BTC/ETH/SOL/XRP/DOGE/ZEC) dają teraz tylko
  **~7%/rok na nominale, ~5.6% na kapitale** (walk-forward), nie ~18% z UNIVERSE_SCAN
  (tamto był gorętszy reżim); (b) detektor jakości złapał REALNY BŁĄD: VELVET/TAC
  mają funding co **4h**, HYPE co **1h**, nie 8h — a annualizacja zakładała 8h, więc
  ich liczby były zaniżone ~2-8×, i były BŁĘDNIE mieszane do portfela po numerze
  rozliczenia. **Fix:** `infer_settle_per_year()` wyprowadza realny interwał z
  timestampów; `analyze_funding`/`simulate_carry_*` przyjmują `settle_per_year`;
  study_funding annualizuje per aktywo poprawnie i liczy portfel TYLKO na 8h-aktywach
  (bez mieszania interwałów), a non-8h pokazuje osobno. Po fixie: VELVET ~29.5%,
  TAC ~29%, HYPE ~34.5%/rok (poprawnie annualizowane). **Strategiczny wniosek: realny
  zwrot jest w wysoko-funding altach 4h/1h (~29-35%), NIE w majorsach (~5-7%) — ale
  te alty są nowsze/ryzykowniejsze (wysoki funding = zapłata za ryzyko). To realne
  dane wskazujące gdzie jest zysk.** Re-run study_funding da poprawiony CARRY_VERDICT.md.
- ⚠️ **KLUCZOWE ODKRYCIE (realny werdykt 2026-07-04 po fixie): 8h-majorsy OOS ≈ 0%.**
  Walk-forward na kapitale dla majorsów (BTC/ETH/SOL/XRP/DOGE) = **+0.38%/rok** przy
  drawdownie 3.4-7.3%. Nasze „~18%" było IN-SAMPLE z gorętszego reżimu — funding
  ostygł, a walk-forward to obnażył. Bezpieczny carry na majorsach TERAZ się nie
  opłaca. Realny zwrot jest tylko w altach 4h: VELVET ~29.5%, TAC ~29% (per-aktywo),
  ale to nowe/ryzykowne kontrakty i NIE były jeszcze walidowane OOS.
- ✅ **Tier A #5: walk-forward dla altów o niestandardowym interwale** — `simulate_
  portfolio` i `walk_forward` przyjmują `settle_per_year` (domyślnie 3×365 = zero
  regresji); study_funding grupuje aktywa non-8h po interwale i liczy walk-forward
  OSOBNO per grupa z poprawnym settle_per_year (mieszać wolno tylko ten sam interwał).
  Cel: sprawdzić, czy 29% VELVET/TAC trzyma się OUT-OF-SAMPLE, czy to in-sample miraż
  jak „18%" na majorsach. **NASTĘPNY KROK: właściciel odpala re-run study_funding →
  wiersz „WALK-FORWARD alty 4h" pokaże uczciwą liczbę OOS altów. Dopiero ona decyduje,
  czy wchodzić w te alty.** (Bez tego wchodzenie = powtórzenie błędu z majorsami.)
- ⚠️⚠️ **KRYTYCZNE (realny werdykt): alty 4h OOS = +16.15%/rok kapitał, ale maxDD
  0.21% / Calmar 102 to FAŁSZYWE ryzyko.** Walk-forward mierzy tylko drawdown STRUMIENIA
  FUNDING — VELVET/TAC mają 99% dodatnich rozliczeń, więc krzywa funding rośnie gładko.
  To IGNORUJE realne ryzyko: **likwidację nogi short przy gwałtownym wzroście ceny alta**
  (VELVET/TAC to zmienne nowe kontrakty). Wysoki funding = zapłata za tę zmienność.
  Calmar 102 = sygnał, że mierzymy złe ryzyko, NIE że to maszynka do pieniędzy.
- ✅ **Tier A #6: ocena ryzyka likwidacji** (`backend/research/liquidation_risk.py` +
  `scripts/study_alt_risk.py`) — mierzy ryzyko, którego funding-study nie widzi:
  `fetch_klines` pobiera realną historię CEN (perp klines), `assess_short_liquidation`
  sprawdza, czy historyczny ruch ceny w górę przekroczył próg likwidacji shorta
  (3x→+31%, 4x→+23%, 5x→+18%). Zapisuje LIQUIDATION_RISK.md. **NASTĘPNY KROK właściciela:
  `python scripts/study_alt_risk.py` na maszynie z Binance → pokaże, czy VELVET/TAC/HYPE
  kiedykolwiek by zlikwidowały short. DOPIERO to (razem z 16% funding) mówi, czy i jaką
  dźwignią wolno wejść w alty.**

- ✅ **Tier A #7: KOREKTA ryzyka — isolated vs cross margin** (`DeltaNeutralCrossStress`
  w risk/margin.py). Realny werdykt `study_alt_risk.py` pokazał, że VELVET/TAC robiły
  historycznie **+147%/+287%** — w ISOLATED margin to pewna likwidacja shorta. ALE to
  był błędny (najgorszy) tryb: w delta-neutral z **CROSS/portfolio margin zysk na nodze
  LONG SPOT jest collateralem dla perpa**, więc czysty ruch kierunkowy NIE likwiduje.
  Smoke na realnych ruchach: isolated 3x → XRP/ZEC/VELVET/TAC LIKW; **cross → WSZYSTKIE
  przeżywają**, z buforem basis +85-117% (perp musiałby wystrzelić >85% PONAD spot, by
  zlikwidować — ekstremalny squeeze). **Skorygowany wniosek: ~16% z altów JEST bezpiecznie
  zbieralne, ale WYMAGA cross/portfolio margin (nie isolated!).** Realne pozostałe ryzyka
  (których cross nie usuwa): płynność wyjścia przy pumpie, flip funding, depeg/venue,
  rozjazd basis ponad bufor. study_alt_risk pokazuje teraz kolumny isolated LIKW + cross
  przeżywa + bufor-basis. Naprawiony też błąd „najg.1bar 0.0%" (intra-bar high/open).

- ✅ **Tier A #8: doradca bezpiecznych wejść w alty** (`AltCarryAdvisor` w
  strategy/alt_carry.py) — kodyfikuje CAŁĄ analizę bezpieczeństwa w twarde reguły
  per alt: (1) cross/portfolio margin ZAWSZE wymagany, (2) min. funding OOS (HYPE 3%
  odpada), (3) dźwignia dobrana tak, by bufor basis ≥ próg (cross), (4) płynność
  wejścia+wyjścia (nominał ścinany/wykluczany), (5) cap per alt, (6) min. historia
  (świeże kontrakty wykluczane). Na realnych liczbach: VELVET/TAC → WCHODŹ 3x
  (bufor 102%/85%, cross), HYPE → ODPUŚĆ (funding za niski). To NIE otwiera pozycji —
  daje werdykt include/dźwignia/nominał per alt. Realne alt-trading nadal wymaga
  live transportu (testnet najpierw) — ten doradca to gotowa, zwalidowana warstwa
  decyzyjna na tamtą fazę.
- ✅ **Tier A #9: test E2E „cały bot w parze z GUI"** (`tests/test_gui_api.py::
  test_full_pipeline_flows_to_gui_contract`) — dowód, że pełny `Pipeline` i
  `GuiApiServer` na JEDNEJ szynie eventów grają razem: dyslokacja przechodzi cały
  łańcuch (sygnał→ryzyko→zlecenie→fill→pozycja), a przekroczenie momentu rozliczenia
  funding daje PnL — i KAŻDY typ kontraktu GUI (market/signal/order/fill/position/
  pnl/risk) faktycznie wypływa do klientów (przechwyt przez override `_broadcast`,
  bo bez podłączonego klienta WS broadcast wychodzi pusty). Sprawdza też treść
  (basisBps≈60, perpQty<0 = short, seria PnL niepusta, decyzja ryzyka). Wcześniejsze
  testy GUI pokrywały translację `event_to_gui` i round-trip WS w izolacji; ten
  domyka lukę integracyjną: backend → kontrakt → GUI jako jeden przepływ.

- ✅ **Audyt całego projektu — bugfixy (branch handoff):** przeskanowane moduły
  krytyczne dla pieniędzy/bezpieczeństwa. Naprawione 3 realne błędy + regresje:
  1. **Funding liczony podwójnie w `PnLSnapshot.net`** (HIGH): `unrealized_pnl`
     zawierał funding, a `book.snapshot` sumował go do `unrealized` I dodawał osobno
     `funding_collected` → net dla OTWARTYCH pozycji zawyżony o funding (dla carry to
     główny zysk → nagłówkowy PnL na GUI ~2× zawyżony podczas trzymania). Fix: dodano
     `Position.price_pnl` (czysty MtM); snapshot liczy MtM, funding wchodzi RAZ przez
     `funding_collected`; karta GUI pokazuje `price_pnl` + osobno `fundingAccrued`.
  2. **MarginWatchdog liczył zdrowie jednym rate'em dla wszystkich aktywów** (HIGH):
     `DEFAULT_MAINTENANCE_BY_ASSET` był tylko w stress-testerze, nie w LIVE watchdogu →
     alty (realne ~0.02) monitorowane rate'em majorsa (~0.005) → zdrowie zawyżone ~4× →
     kontrolowany flatten za późno na dokładnie tych zmiennych altach, które strategia
     teraz celuje. Fix: watchdog przyjmuje `maintenance_by_asset`, Pipeline podaje
     tabelę; bez tabeli zachowanie bez zmian (zero regresji dla istniejących testów).
  3. **Filtr `SymbolFilters.ok()` (minQty/minNotional) był martwy** (MED): OrderManager
     kwantyzował, ale nie sprawdzał minimum → zlecenia poniżej minimum szły na giełdę
     (reject/orphan). Fix: `_submit` odrzuca sub-minimum PRZED wysyłką → para zostaje
     flat (invariant), zero marnowanych prób. (opt-in, dotyczy ścieżki live z filtrami).
  Suita 312→**316** (regresje: double-count funding, per-asset mmr watchdog +
  brak-regresji, min-notional reject). **Znalezione, NIE zmienione (czeka na decyzję
  właściciela):** (a) „dzienny" limit straty NIE jest dzienny — `reset_day()` nigdy nie
  wołany, a `_on_pnl` nadpisuje `realized_pnl_today` skumulowanym realized; dodatkowo
  guard (Monitor+Risk) patrzy TYLKO na `realized` (dla delta-neutral ≈ 0) i ignoruje
  `unrealized` — czyli rozjazd basis (realny drawdown carry) NIE zatrzyma bota;
  (b) live close bez `reduceOnly`/positionSide (na koncie hedge-mode BUY otworzyłby
  long zamiast domknąć short — testnet-gated); (c) `ManagedOrder.avg_price` bierze
  cenę ostatniego filla zamiast średniej ważonej (tylko informacyjnie); (d) nogi liczone
  jako N/spot vs N/perp → resztkowa delta ~basis (w tolerancji). Rekomendacja: (a) to
  najważniejsze — przerobić guard na drawdown NETTO (z unrealized) + realny rollover dnia.

- ✅ **Audyt #2 (recenzja zewnętrzna) — WSZYSTKIE punkty naprawione:** właściciel
  wkleił niezależny przegląd; każdy zarzut zweryfikowany w kodzie i potwierdzony:
  1. **P0 close/flatten po cenie WEJŚCIA** — paper broker fill'uje po `req.price`,
     więc cały ruch ceny od entry znikał z realized (PnL fabrykowany). Fix:
     `close_pair`/`_flatten` przyjmują ceny rynkowe, `ExecutionEngine._close`
     podaje ceny z ostatniego ticka (fallback do entry, gdy ceny brak).
  2. **P0 milczący feed nie zatrzymywał bota** — STALE_FEED z adaptera powstaje
     dopiero przy NASTĘPNYM ticku; przy pełnej ciszy nigdy. Fix: `Monitor.
     start_heartbeat()` (task cykliczny) emituje EMERGENCY_STOP, gdy `is_stale()`;
     runner startuje go w trybie live i zatrzymuje w finally.
  3. **P1 raport/backtest ignorował unrealized otwartych pozycji** — carry celowo
     TRZYMA, więc net był metodologicznie mylący. Fix: `build_report(..., marks=)`
     + `DailyReport.unrealized`; `BacktestResult.final_unrealized`; net zawiera
     mark-to-market (`ExecutionEngine.marks` property).
  4. **P1 live futures close bez reduceOnly** — w hedge-mode BUY otworzyłby LONGA
     zamiast domknąć shorta. Fix: `reduceOnly=true` dla PERP+CLOSE. Oraz **price=0
     omijał limit nominału** (0×qty=0≤limit): submit odrzuca `price<=0`; smoke-test
     wymaga `--price > 0`.
  5. **P1 GUI nie usuwał zamkniętych pozycji** — POSITION_CLOSED wyglądał jak
     otwarcie. Fix: kontrakt ma `closed: bool`; `js/app.js` robi delete zamiast set.
  6. **Strategia: payback kosztu w carry** — próg min_funding 0.1bps przepuszczał
     wejścia spłacające prowizje ~60 dni. Fix: `carry_max_payback_settles=30`
     (koszt round-trip musi zwrócić się z funding w ≤30 rozliczeń = 10 dni @8h);
     konfigurowalne.
  7. **Drobne:** FAIR_VALUE publikowany na szynę (audyt); GUI assets 4→9 (config.js
     + index.html select); README stare ścieżki `gui/` poprawione; deprecation
     `utcfromtimestamp` → timezone-aware; **DB batch commit ticków** (co 25;
     sygnały/fille/decyzje nadal commit natychmiast) — commit-per-event dusił
     pipeline przy żywym feedzie.
  Suita 316→**328** (12 nowych regresji). NIE zrobione z recenzji (świadomie):
  refaktor EventBus na współbieżny (duża zmiana, sekwencyjność jest też gwarancją
  porządku zdarzeń — batch commit DB adresuje realny koszt); min-hold w polityce
  (EMA exit już tłumi churn; payback-gate tnie problem u źródła).

- ✅ **Audyt #3 — guard drawdownu NETTO + rollover doby (ostatnie znane luki):**
  1. **Monitor: limit dzienny liczy NETTO** (realized + unrealized + funding − fees)
     jako deltę od początku doby UTC. Wcześniej patrzył tylko na realized — dla
     delta-neutral carry realized ≈ 0 przez cały czas trzymania, więc rozjazd basis
     (realny drawdown) NIGDY nie zatrzymałby bota. Rollover doby po ts eventu:
     wielodniowa sesja nie kumuluje wczorajszej straty do dzisiejszego limitu.
  2. **RiskManager: liczniki dzienne naprawdę dzienne** — `_maybe_rollover(ts)`
     (z ticków i PNL_UPDATE) resetuje trades_today i bazę realized;
     `realized_pnl_today` = delta od początku doby, nie skumulowane od startu.
  3. **avg_price ważone ilością** (`_apply_fill_to_order`) — cena ostatniego filla
     przekłamywała średnią po dosyłce reszty po partial.
  4. **Zero resztkowej delty przy otwarciu** — obie nogi na TEJ SAMEJ ilości
     bazowej (hedge znosi się per sztuka); qty_perp = notional/perp zostawiał
     deltę ~basis na każdej parze.
  Suita 328→**333**. Tym samym WSZYSTKIE punkty z obu audytów (własnego i
  zewnętrznego) są zamknięte.

- ✅ **CI + lint (poziom enterprise):** `.github/workflows/ci.yml` — każdy push/PR
  odpala pełną suitę pytest + ruff na Pythonie 3.11 i 3.12. `ruff.toml` (E/W/F,
  bez E501). Naprawione znalezione problemy lintu (nieużywane importy, `l` → `lev`).
- ✅ **Crash-safe recovery (odzysk po padzie):** księga żyła w RAM, DB było
  `:memory:` — pad procesu z otwartymi pozycjami = bot po restarcie NIE WIE
  o własnym hedge'u na giełdzie. Teraz: `backend/execution/recovery.py::
  restore_book(db, book)` — chronologiczny replay trwałego audit trailu (tabela
  `fills` + eventy FUNDING_ACCRUED) przez TĘ SAMĄ logikę księgowania, więc stan po
  odbudowie = stan sprzed padu (test-inwariant). Runner: przy plikowej DB odbudowuje
  `pipe.book`, przywraca ekspozycję ryzyka (`restore_exposure` — bez podbijania
  trades_today) i nadzór polityki (`restore_holding` — nie dubluje wejścia, pilnuje
  wyjścia). `run_paper_live --db PATH`; live domyślnie `data/onewish_live.db`
  (synthetic dalej `:memory:`). Nieznane aktywa w starej DB pomijane z logiem.
- ✅ **Raport per sesja + UX:** granica sesji po rowid (ts nie rozdziela — SimClock
  startuje od zera), Ctrl+C wypisuje raport, snapshot GUI odtwarza trzymane
  pozycje nowym klientom (także po recovery), zamknięte znikają z cache.
- ✅ **Transport WebSocket dla danych live** (`backend/adapters/market/binance_ws.py`)
  — streamy zamiast pollingu REST 2s: spot bookTicker + perp bookTicker +
  markPrice@1s (mark/index/`r`/T). `r` ze streamu = ŻYWA estymata forward funding
  liczona przez giełdę (lepsza niż rekonstrukcja premia+clamp — zawiera interest).
  Architektura jak w live adapterze: czysty `TickAssembler` (parsowanie → stan per
  aktywo → MarketTick; throttle `min_emit_interval`; głębokość = notional
  top-of-book; data_lag z czasu eventu giełdy) w pełni testowany offline; jedyny
  styk I/O to `_pump` (websockets.connect, reconnect z backoffem wykładniczym,
  sufit 30s). Dwa połączenia (spot+futures) zasilają wspólną kolejkę. Opt-in:
  `run_paper_live.py --transport ws` (REST zostaje domyślny — zero regresji);
  świeżość pilnują istniejące STALE_FEED + heartbeat. **Nie zweryfikowane na żywym
  Binance** (sandbox blokuje) — pierwsze odpalenie na maszynie właściciela:
  `python scripts/run_paper_live.py --mode live --transport ws --budget-pln 150`.

- ✅ **Tier B krok 1: warstwa POMIAROWA multi-venue** — zanim powstanie egzekucja
  na innych giełdach, mierzymy czy jest o co grać (metodologia jak Tier A):
  `backend/adapters/market/venues.py` (publiczne historie funding Bybit/OKX;
  czyste parsery na fixture'ach, jedyny styk I/O `_http_get_json`; mapy symboli
  z jawnym None dla nienotowanych — VELVET/TAC tylko Binance),
  `backend/research/multi_venue.py` (annualizacja z REALNEGO interwału rozliczeń
  — mediana odstępów timestampów; `VenueComparison.best` = najlepsze venue dla
  nogi short; `uplift_vs(BINANCE)` w pp/rok; `summarize` sortuje po uplifcie),
  `scripts/study_venues.py` → tabela + `VENUE_SCAN.md`. **Uplift jest BRUTTO** —
  przed kosztami venue i ryzykiem kontrahenta; tabela mówi CZY budować Tier B,
  nie że wolno wchodzić. Wymaga sieci → odpalić na maszynie właściciela:
  `python scripts/study_venues.py`. Decyzja: uplift ≥ kilka pp/rok = budujemy
  egzekucję Tier B; ~0 pp = Binance-only wystarcza i Tier B nie jest wart ryzyka.

**Zostało (buildable-now):**

9. **⭐ DECYZJA WŁAŚCICIELA: forward paper-trade na ŻYWYM rynku z fikcyjnym budżetem
   ~150 zł.** Mechanizm GOTOWY i jednokomendowy:
   `python scripts/run_paper_live.py --mode live --budget-pln 150 --no-gui`.
   Żywe dane Binance (read-only) + egzekucja PAPIEROWA + limit kapitału z budżetu +
   **automatyczne zmniejszenie rozmiaru pozycji do capu** (bez tego ryzyko odrzucało
   KAŻDE wejście, bo nominał > cap — naprawione i przetestowane) + tracking „ile z 150
   zł zostało" (snapshot w raporcie). Budżet 150 zł ≈ $37 (USD_PER_PLN=0.25). To NIE
   jest mainnet/realny handel. **WYMAGA dostępu sieci do Binance:** sandbox tej sesji
   blokuje `api.binance.com` (403 z proxy) — odpalaj na środowisku z polityką sieci
   dopuszczającą Binance (Twój PC albo remote z odpowiednią polityką). UWAGA sizing:
   perp minNotional BTC $50 / ETH $20 — przy $37 celuj w tańsze alty (DOGE/XRP); BTC
   poza zasięgiem. Cel: uczciwy test zachowania przed jakimkolwiek mainnetem.
10. **(potem, po pozytywnym pilocie na testnecie + paper-live)** kontrolowany pilot
    live na minimalnych stawkach (`allow_mainnet=True`, świadoma decyzja właściciela).

Overlay kierunkowy (BTC→alty lead-lag) — opcja „wyższy zwrot/ryzyko", NIE potrzebny
jako fallback (carry przeszedł).

## 8. Współpraca z Codexem (GitHub)

- Projekt jest/idzie na GitHub (Codex to robił). Pracujemy na wspólnym remote.
- **Podział:** Claude — backend/strategia/testy; Codex — GUI/frontend + recenzje.
- **Branże + PR-y:** pracuj na osobnej gałęzi, pushuj, recenzja przez diff/PR.
  `pull` przed pracą; małe commity; jasne opisy; nie edytujcie jednego pliku naraz.
- **Pętla recenzji:** właściciel mówi „Codex sprawdź" → Codex proponuje → Claude
  przechodzi punkt po punkcie, oddziela realne od szumu, implementuje to, co warto.
  (Ta pętla już 2× wyłapała realne bugi — działa.)

## 9. Bezpieczeństwo — czego NIE robić

- **Nie włączaj realnego handlu na MAINNECIE.** `binance_live` ma już realny transport,
  ale jest domyślnie zablokowany wieloma bramkami: live_enabled, arm(token
  "I_UNDERSTAND_REAL_MONEY"), klucze z env, limit nominału, `transport_implemented=False`
  domyślnie, oraz `testnet=True` (mainnet wymaga osobnego `allow_mainnet=True`). Kolejność:
  najpierw testnet (smoke + pilot), potem dopiero świadoma decyzja o mainnecie.
- **Sekrety** (klucze, portfel) tylko z env (`ONEWISH_BINANCE_KEY/SECRET`), nigdy w repo.
- Recorder i wszystkie skrypty danych to **tylko odczyt** publicznych endpointów — bezpieczne.
- Każda zmiana z testem. Utrzymuj `pytest -q` na zielono.

## 10. Kluczowy kontekst (nieoczywisty z kodu)

- **Carry-entry był zbugem:** detektor pierwotnie bramkował wejście dyslokacją
  (rzadką) → 0 wejść na realnych danych → bot nie łapałby własnego edge. Naprawione:
  entry_mode="carry" domyślny, próg głębokości względny (depth_mult×notional), lag
  rozluźniony (był to skew zegara, nie nieświeżość), min_funding obniżony do 0.1 bps.
- **Operacyjne z exchangeInfo:** perp minNotional > spot (BTC $50 vs $5), perp step
  grubszy → min pozycja ~$50–60, sizing w wielokrotnościach kroku perpa.
- **Smoothed >> churn:** wyjście na każdym ujemnym funding pali prowizje (BTC −22%/rok
  vs +10% always-in) — dlatego EMA funding exit.
- Werdykt carry NIE wymaga zbierania ticków na żywo — historia funding (study_funding)
  daje go natychmiast. Live-tick replay to doszlifowanie (basis/poślizg).
