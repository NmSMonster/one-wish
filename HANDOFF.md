# ONE WISH — Handoff dla sesji Claude Code (stan + plan)

> Czytasz to jako świeża sesja bez wcześniejszej pamięci. Ten plik jest
> samowystarczalnym źródłem prawdy. Właściciel (Borys) jest na wyjeździe i pisze
> **z telefonu** — krótkie wiadomości. Bądź konkretny, działaj samodzielnie, nie
> zalewaj tekstem. Język rozmowy: polski.

## TL;DR

One Wish to **realny bot tradingowy**: delta-neutral **basis/funding carry** na
Binance (long spot + short perp, inkasowanie funding). Backend Python, event-driven,
**158 testów pytest zielonych**. Edge (carry) **zwalidowany na ~roku realnej historii
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

## 2. Status — co działa (158 testów zielonych)

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
    exchange/  paper (PaperBroker), binance_live (ZABLOKOWANY, transport off)
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
  app/         pipeline (montaż wszystkiego), runner (OneWishApp), report
  backtest/    engine (Backtester), metrics
  research/    edge_validation (M3.5), funding_study (werdykt carry),
               universe (ranking aktywów), recorder (zapis ticków)
scripts/       study_funding, run_backtest, run_edge_validation, run_paper_live,
               record_market, record_liquidations, scan_universe
tests/         pełna suita pytest (158)
Dokumenty:     README, ONE_WISH_STRATEGY.md, ARCHITECTURE.md, EDGE_VALIDATION.md,
               CARRY_VERDICT.md, UNIVERSE_SCAN.md, DATA_CONTRACT.md, ten HANDOFF.md
```

## 6. Jak uruchomić

```
python -m pytest -q                              # 158 testów
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
  raport per pozycja + summary (likwidacja/flatten/safe). Wciąż TODO: pełny model
  gotówki spot-wallet vs futures-wallet (alokacja kapitału między nogami).
- ◑ **#7 chaos-testy (część)** — dodane: martwa noga (perp nie domyka), mieszany
  chaos spot-partial+perp-dead, restart między nogami + recovery do flat,
  flatten-noop. Wciąż TODO: stale feed mid-sequence, DB write fail, lost-ack
  (lost-ack świadomie odłożony do live transport — wymaga reconcile-before-retry,
  inaczej ryzyko podwójnego filla).

**Zostało (buildable-now):**

4. **#8 alerty poza GUI + runbook** — Telegram/Discord/email (orphan_leg_age,
   net_delta, margin_health, funding divergence, data_lag, kill_state) + runbook
   operatora.
5. **#3 funding reconciliation** — ledger realnego funding z konta vs model (z kluczami).
6. **#2 cost telemetry/shadow** — pomiar realnego spreadu/poślizgu/latencji.
7. **Wpięcie rozszerzonego uniwersum do live** — dodać DOGE/ZEC/VELVET/TAC/HYPE
   (z UNIVERSE_SCAN.md); rusza Asset enum (core/types) + listę aktywów w GUI (Codex).
   Sizing licz w wielokrotnościach kroku PERPA (perp minNotional BTC $50, ETH $20!).
8. **Realny transport na TESTNECIE** — podpisane zlecenia spot+perp, stan konta,
   reconcyliacja, na `testnet.binancefuture.com` (fałszywe pieniądze, zero ryzyka).
   binance_live ma `transport_implemented=False`. To krok do live-capable.
9. **(potem, po pozytywnym pilocie)** kontrolowany pilot live na minimalnych stawkach.

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

- **Nie włączaj realnego handlu.** `binance_live` jest zablokowany (live_enabled,
  arm(token "I_UNDERSTAND_REAL_MONEY"), klucze z env, limity, transport_implemented=
  False). Realny handel dopiero po: testnet + pozytywny werdykt + świadoma decyzja
  właściciela.
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
