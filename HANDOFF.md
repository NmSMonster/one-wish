# ONE WISH — Handoff dla sesji Claude Code (stan + plan)

> Czytasz to jako świeża sesja bez wcześniejszej pamięci. Ten plik jest
> samowystarczalnym źródłem prawdy. Właściciel (Borys) jest na wyjeździe i pisze
> **z telefonu** — krótkie wiadomości. Bądź konkretny, działaj samodzielnie, nie
> zalewaj tekstem. Język rozmowy: polski.

## TL;DR

One Wish to **realny bot tradingowy**: delta-neutral **basis/funding carry** na
Binance (long spot + short perp, inkasowanie funding). Backend Python, event-driven,
**235 testów pytest zielonych**. Edge (carry) **zwalidowany na ~roku realnej historii
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

## 2. Status — co działa (235 testów zielonych)

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
               universe (ranking aktywów), recorder (zapis ticków)
scripts/       study_funding, run_backtest, run_edge_validation, run_paper_live,
               record_market, record_liquidations, scan_universe, run_testnet_smoke
tests/         pełna suita pytest (235)
Dokumenty:     README, ONE_WISH_STRATEGY.md, ARCHITECTURE.md, EDGE_VALIDATION.md,
               CARRY_VERDICT.md, UNIVERSE_SCAN.md, DATA_CONTRACT.md, RUNBOOK.md, ten HANDOFF.md
```

## 6. Jak uruchomić

```
python -m pytest -q                              # 235 testów
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
