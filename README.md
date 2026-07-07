# One Wish

Bot do realnego tradingu na Binance (spot + USDT-M perpetuals) w strategii
**delta-neutral basis/funding** — przeniesienie idei „opóźnionej wyceny" na
produkt dostępny legalnie z Polski. Architektura event-driven, GUI tylko
renderujące, bezpieczeństwo „read-only → paper → backtest → paper-live → dopiero
realny pilot".

> ⚠️ **Realny handel jest domyślnie ZABLOKOWANY.** Patrz `EDGE_VALIDATION.md` —
> na realnych danych Binance strategia v0 nie ma jeszcze udowodnionego edge po
> kosztach. Do czasu pozytywnej, wielodniowej walidacji bot działa wyłącznie w
> trybie paper.

## Status

Zrealizowane M1–M12 + brama M3.5. Cały backend pokryty testami (pytest).

| Etap | Co | Status |
|---|---|---|
| M1 | Strategia (`ONE_WISH_STRATEGY.md`) | ✅ |
| M2 | Architektura (`ARCHITECTURE.md`) | ✅ |
| M3 | Market Data Adapter (Binance read-only + synthetic + replay) + Storage | ✅ |
| M3.5 | Brama walidacji edge (`EDGE_VALIDATION.md`) | ✅ (live: FAIL — paper only) |
| M4 | Fair Value Model | ✅ |
| M5 | Repricing Detector + model kosztów | ✅ |
| M6 | Risk Manager + `risk_config.yaml` | ✅ |
| M7 | Strategy Policy + Paper Execution + Position book | ✅ |
| M8 | Audit trail + odtwarzanie decyzji | ✅ |
| M9 | GUI API (WebSocket) | ✅ |
| M10 | Backtest + metryki | ✅ |
| M11 | Runner paper-live + raport dzienny + monitoring | ✅ |
| M12 | Realny adapter Binance — **zablokowany**, podwójne zabezpieczenie | ✅ |

## Instalacja

```bash
python -m pip install -r requirements.txt
```

## Uruchamianie

```bash
# Testy (cała suita)
python -m pytest -q

# ⭐ Werdykt CARRY na realnej ~rocznej historii funding (natychmiast, bez zbierania):
python scripts/study_funding.py        # -> CARRY_VERDICT.md (carry ~+18%/rok delta-neutral)

# Brama walidacji edge — syntetyk + realny snapshot Binance (strona dyslokacji)
python scripts/run_edge_validation.py

# Backtest — porównanie strategii carry vs scalp (syntetyk)
python scripts/run_backtest.py

# Nagraj realne dane Binance (na godziny/dni, Ctrl+C kończy) i zweryfikuj edge.
# record_market zapisuje: ceny, FORWARD funding (mark+premia), open interest,
# realny depth + sidecar z historią funding (reżim).
python scripts/record_market.py --out data/binance_ticks.jsonl --cycles 0 --interval 2
# (opcjonalnie, osobny terminal) strumień likwidacji = timing wejścia:
python scripts/record_liquidations.py --out data/liquidations.jsonl
# werdykt:
python scripts/run_backtest.py --data data/binance_ticks.jsonl
python scripts/run_edge_validation.py --data data/binance_ticks.jsonl

# Runner paper-live:
#   synthetic (offline, do GUI):
python scripts/run_paper_live.py --mode synthetic --watch
#   live (realne dane Binance read-only, egzekucja NADAL paper):
python scripts/run_paper_live.py --mode live --cycles 60
#   live długoterminowo: fikcyjny budżet + sizing ważony funding + streamy WS;
#   trwała baza (data/onewish_live.db) daje crash-safe recovery po restarcie:
python scripts/run_paper_live.py --mode live --budget-pln 150 --funding-weighted --transport ws
```

CI: każdy push/PR przechodzi pełną suitę pytest + ruff (`.github/workflows/ci.yml`).
Procedury operacyjne (alerty, recovery po padzie, stress-test marginu): `RUNBOOK.md`.
Protokół walidacji S1→S5 (kryteria PASS/FAIL od sandboxa po pilot live): `VALIDATION.md`.
Windows: dwuklik `start_live.bat` (bot na żywych danych) + `start_gui.bat` (cockpit)
+ `run_verdicts.bat` (werdykty pomiarowe Tier A/B).

### GUI (cockpit)

GUI jest statyczne (pliki w katalogu repo: `index.html`, `js/`, `styles.css`) i
tylko renderuje dane z backendu. Domyślnie chodzi w trybie `mock` (baner
SIMULATION). Aby podpiąć live:

1. uruchom runner z GUI: `python scripts/run_paper_live.py --mode synthetic --watch`
2. w `js/config.js` ustaw `adapter: "ws"` (URL `ws://127.0.0.1:8765/gui`),
3. zaserwuj pliki: `python -m http.server 8080` i otwórz `http://127.0.0.1:8080/index.html`.

Kontrakt danych: `ARCHITECTURE.md` §5 / `DATA_CONTRACT.md`. GUI nie podejmuje
żadnych decyzji tradingowych — wysyła co najwyżej komendy operatora (kill/flatten).

## Architektura (skrót)

Event-driven: moduły gadają przez `EventBus`, każdy istotny krok to event
zapisany do audit trail (sqlite), więc każdą decyzję da się odtworzyć.

```
Binance/synthetic → MarketDataAdapter → FairValueModel → RepricingDetector
   → StrategyPolicy → RiskManager(weto) → ExecutionEngine → PaperBroker
   → PositionBook(PnL) → EventBus → {Database(audit), GUI API(WS), Monitoring}
```

Pełny opis i odpowiedzialności modułów: `ARCHITECTURE.md`.

## Bezpieczeństwo

- **Domyślny tryb = paper.** Realny `BinanceLiveAdapter` jest za czterema bramkami
  (`live_enabled` + klucze z env + jawny `arm(token)` + limit nominału) i ma
  świadomie **wyłączony transport** — nie wyśle zlecenia.
- **Risk Manager** ma prawo weta (limity straty/ekspozycji/płynności) + kill switch.
- **Monitoring** emituje EMERGENCY_STOP (flatten + kill) przy przekroczeniu limitu.
- **Sekrety** (klucze, portfel) nigdy w repo — tylko env (`ONEWISH_BINANCE_KEY/SECRET`).

## Struktura

```
backend/   core, adapters (market/exchange), model, signal, strategy, risk,
           execution, monitoring, storage, api, app (pipeline/runner/report),
           backtest, research (brama edge)
gui/       statyczny cockpit (WebSocket → kontrakt)
scripts/   study_funding, run_edge_validation, run_backtest, run_paper_live, record_market, record_liquidations
tests/     pełna suita pytest
```

## Strategia (skrót)

Delta-neutral **carry**: wejście na korzystnej dyslokacji (perp drogi → dobry dla
short perp), trzymanie pary long-spot/short-perp przez kolejne rozliczenia funding
(short perp inkasuje funding), wyjście gdy nośność znika (funding ≤ próg) lub basis
odwróci się poza stop. Tryb `scalp` (churn na dyslokacji) zostawiony do porównań.
Szczegóły: `ONE_WISH_STRATEGY.md`.

## Werdykt edge

Carry zwalidowany na realnej ~rocznej historii funding: **~+18%/rok delta-neutral po
prowizjach** (BTC/ETH/SOL/XRP, 80–91% rozliczeń dodatnich) — patrz `CARRY_VERDICT.md`.
Polityka wyjścia używa wygładzonego (EMA) sygnału funding, żeby nie churnować na
pojedynczym ujemnym ticku (smoothed ≫ pos-only na realnych danych).

## Dalsze kroki

1. (opcjonalnie, doprecyzowanie) Zebrać realne ticki (`record_market.py`) i zrobić
   pełny replay z konwergencją basis i poślizgiem (`run_backtest.py --data`).
2. **Realny transport Binance** (`binance_live` — dziś `transport_implemented=False`):
   podpisane zlecenia spot+perp, reconcyliacja, klucze z env.
3. **Kontrolowany pilot live** (M12): minimalne stawki, świadome `arm()`, ręczny+auto
   kill switch — dopiero po przejściu testów na żywo w paper.
4. (opcjonalnie, wyższy zwrot/ryzyko) overlay kierunkowy BTC→alty (framework gotowy).
