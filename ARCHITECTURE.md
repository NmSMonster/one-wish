# ONE WISH — Architektura (M2)

> Szkielet systemu. Definiuje moduły, ich odpowiedzialności, przepływ danych i
> strukturę katalogów. **Bez implementacji realnego handlu.** Spina się 1:1 z
> taktyką z `ONE_WISH_STRATEGY.md`.

---

## 1. Zasady architektury

1. **Event-driven.** Moduły komunikują się przez `Event Bus`, nie wołają się bezpośrednio. Każda decyzja jest eventem → audytowalna i odtwarzalna (M8).
2. **GUI tylko czyta.** Frontend nie podejmuje żadnych decyzji tradingowych — renderuje stan i wysyła co najwyżej komendy operatora (np. kill switch).
3. **Adapter pattern.** Rynek siedzi za interfejsem (`Market Data Adapter` + `Exchange Adapter`). Binance to pierwsza implementacja; Paper i ewentualny Kraken/Bybit to kolejne — rdzeń ich nie rozróżnia.
4. **Koszty wbudowane od początku.** `FairValueModel` i `RepricingDetector` liczą edge **netto** (fee, poślizg, spread, funding) — nie dopiero w backteście.
5. **Kill switch wszędzie.** Każdy moduł może wyemitować `EMERGENCY_STOP`; Execution reaguje natychmiast (flatten + blokada wejść).
6. **Live trading domyślnie wyłączony.** Realny `Exchange Adapter` jest za podwójną blokadą (M12). Domyślny tryb to paper.
7. **Sekrety poza repo.** Klucze API / portfel w env / menedżerze sekretów, nigdy w kodzie.

---

## 2. Moduły i odpowiedzialności

| # | Moduł | Odpowiedzialność | Wejście → Wyjście |
|---|---|---|---|
| 1 | **Market Data Adapter** | Strumienie spot/perp/index, funding, orderbook, spread, płynność; pomiar `data_lag`. | Binance WS/REST → eventy `MARKET_TICK` |
| 2 | **Fair Value Model** | Liczy `fair_basis`, oczekiwany funding, koszt nośności, `fair_price`, `confidence`. | `MARKET_TICK` → `FAIR_VALUE` |
| 3 | **Repricing Detector** | Wykrywa dyslokację basis po ruchu spotu; liczy `expected_net_edge` (po kosztach); filtruje fałszywe sygnały. | `MARKET_TICK` + `FAIR_VALUE` → `EDGE_DETECTED` / `EDGE_LOST` / `NO_TRADE_CONDITION` |
| 4 | **Strategy Policy** | Reguły z §4–§6 strategii: czy ten edge to wejście, wyjście czy pas; dobór nóg long-spot/short-perp i wielkości. | `EDGE_*` → `TRADE_INTENT` |
| 5 | **Risk Manager** | Limity (dzienna strata, ekspozycja/aktywo, liczba pozycji, korelacje, spread, płynność, margines); kill switch. Może powiedzieć „NIE". | `TRADE_INTENT` → `RISK_APPROVED` / `RISK_REJECTED` |
| 6 | **Execution Engine** | Tłumaczy zatwierdzony zamiar na zlecenia na obu nogach; wybiera maker/limit; pilnuje delta-neutralności przy wejściu i wyjściu. | `RISK_APPROVED` → `ORDER_REQUEST` |
| 7 | **Order Manager** | Cykl życia zleceń, fille/partial fille/odrzucenia; **reconcyliacja stanu po restarcie**; idempotencja (`client_order_id`). | `ORDER_REQUEST` ↔ Adapter → `ORDER_UPDATE` / `FILL` |
| 8 | **Exchange Adapter** | Realne składanie zleceń. **Paper** (M7) i **Binance live** (M12) za podwójną blokadą. | `ORDER_REQUEST` → giełda/symulacja |
| 9 | **Event Bus** | Centralna szyna eventów; pub/sub; trwałość do audytu. | wszystkie ↔ wszystkie |
| 10 | **Database** | Zapis obserwacji rynku, wycen, sygnałów, zleceń, filli, pozycji, PnL, settlementów. | eventy → storage |
| 11 | **GUI API** | Agreguje stan i streamuje do GUI przez WebSocket (kontrakt §5). Przyjmuje komendy operatora. | eventy → WS / WS → `OPERATOR_COMMAND` |
| 12 | **Monitoring + Kill Switch** | Healthcheck strumieni, alerty, stale-feed/lag/margin watchdog; ręczny i automatyczny kill switch. | metryki → alerty / `EMERGENCY_STOP` |

---

## 3. Przepływ danych (happy path)

```
Binance (spot+perp+funding+orderbook)
      │
      ▼
[1] Market Data Adapter ──MARKET_TICK──▶ [2] Fair Value Model ──FAIR_VALUE──┐
      │                                                                      ▼
      └──────────────MARKET_TICK──────────────────────────▶ [3] Repricing Detector
                                                                      │
                                            EDGE_DETECTED / NO_TRADE  │
                                                                      ▼
                                                            [4] Strategy Policy
                                                                      │ TRADE_INTENT
                                                                      ▼
                                                            [5] Risk Manager
                                                          RISK_APPROVED │ RISK_REJECTED
                                                                      ▼
                                                            [6] Execution Engine
                                                                      │ ORDER_REQUEST
                                                                      ▼
                                                  [7] Order Manager ◀▶ [8] Exchange Adapter
                                                                      │ ORDER_UPDATE / FILL
                                                                      ▼
   wszystkie eventy ──▶ [9] Event Bus ──▶ [10] Database
                                       └──▶ [11] GUI API ──WebSocket──▶ GUI
                                       └──▶ [12] Monitoring ──▶ alerty / EMERGENCY_STOP
```

Ścieżka awaryjna: dowolny moduł lub watchdog → `EMERGENCY_STOP` → Execution flatuje obie nogi + Strategy/Execution blokują nowe wejścia.

---

## 4. Event Bus — katalog eventów

- **Rynek:** `MARKET_TICK`, `FAIR_VALUE`, `DATA_LAG_WARNING`, `STALE_FEED`
- **Sygnał:** `EDGE_DETECTED`, `EDGE_LOST`, `NO_TRADE_CONDITION`
- **Decyzja:** `TRADE_INTENT`, `RISK_APPROVED`, `RISK_REJECTED`
- **Egzekucja:** `ORDER_REQUEST`, `ORDER_UPDATE`, `FILL`, `PARTIAL_FILL`, `ORDER_REJECTED`, `FLATTEN`
- **Pozycja/PnL:** `POSITION_OPENED`, `POSITION_UPDATED`, `POSITION_CLOSED`, `FUNDING_ACCRUED`, `PNL_UPDATE`
- **Ryzyko/awaria:** `RISK_LIMIT_BREACH`, `MARGIN_WARNING`, `EMERGENCY_STOP`, `KILL_SWITCH`
- **Operator:** `OPERATOR_COMMAND` (pause / resume / kill / flatten)

Każdy event: `{ id, ts, type, source, severity, payload }` — zapisywany w DB dla pełnego audit trail (M8) i odtworzenia „dlaczego bot wszedł / nie wszedł".

---

## 5. GUI API — kontrakt (zgodny z GUI od Codexa)

GUI API streamuje przez WebSocket wiadomości JSON (pole `type`). **To jest źródło prawdy dla frontendu** — żadnych danych spoza tego kontraktu:

```
status:   { type, botState, connection, latencyMs, serverTimeUtc }
market:   { type, asset, spot, perp, index, basisBps, fundingRate,
            predictedFunding, nextFundingTs, dataLagMs }
signal:   { type, asset, fairBasisBps, observedBasisBps, dislocationBps,
            expectedNetEdge, state, reason }
position: { type, id, asset, spotQty, spotEntry, perpQty, perpEntry,
            netDelta, unrealizedPnl, fundingAccrued, marginRatio, openedTs }
order:    { type, id, asset, leg, side, orderType, price, qty, status }
fill:     { type, orderId, asset, leg, price, qty, fee, ts }
pnl:      { type, realized, unrealized, fundingCollected, feesPaid, net,
            series:[{ts,value}] }
risk:     { type, dailyLoss:{used,limit}, perAssetExposure:[{asset,used,limit}],
            openPositions:{used,limit}, marginBuffer, lastDecision, reason }
event:    { type, ts, eventType, severity, message, data }
```

Kierunek operator → backend: `{ type:"command", action:"pause|resume|kill|flatten" }`.

---

## 6. Struktura katalogów (propozycja)

```
One Wish/
├─ ONE_WISH_STRATEGY.md      # M1
├─ ARCHITECTURE.md           # M2 (ten plik)
├─ risk_config.yaml          # M6
├─ backend/                  # Python
│  ├─ core/                  # Event Bus, modele, typy eventów
│  ├─ adapters/
│  │  ├─ market/binance.py   # Market Data Adapter
│  │  └─ exchange/
│  │     ├─ paper.py         # Paper Exchange Adapter (M7)
│  │     └─ binance.py       # Real Exchange Adapter (M12, za blokadą)
│  ├─ model/fair_value.py    # Fair Value Model (M4)
│  ├─ signal/repricing.py    # Repricing Detector (M5)
│  ├─ strategy/policy.py     # Strategy Policy
│  ├─ risk/manager.py        # Risk Manager (M6)
│  ├─ execution/             # Execution Engine + Order Manager
│  ├─ storage/               # Database
│  ├─ api/gui_ws.py          # GUI API (WebSocket, kontrakt §5)
│  ├─ monitoring/            # healthcheck, watchdogi, kill switch
│  └─ main.py                # bootstrap, tryb paper/live
├─ gui/                      # GUI od Codexa (index.html, styles.css, js, adaptery)
├─ data/                     # baza obserwacji / backtest
└─ tests/
```

---

## 7. Granice bezpieczeństwa

- **Domyślny tryb = paper.** Live wymaga jawnej flagi + drugiego potwierdzenia (M12).
- **Osobne klucze** dla read-only danych i dla tradingu; portfel pilota minimalny.
- **Watchdogi** (stale feed, data lag, margin ratio) → automatyczny `EMERGENCY_STOP`.
- **Reconcyliacja po restarcie** w Order Managerze: stan z giełdy jest prawdą, nie pamięć procesu.

---

## 8. Status po M2

- ✅ 12 modułów z jasnymi odpowiedzialnościami i interfejsami.
- ✅ Przepływ danych (happy path + ścieżka awaryjna).
- ✅ Katalog eventów Event Busa.
- ✅ Kontrakt GUI API spięty 1:1 z GUI (Codex).
- ✅ Struktura katalogów i granice bezpieczeństwa.
- ⛔ Brak implementacji handlu. Wiemy, co budować — nie podłączamy pieniędzy.

**Następny krok:** M3 — `Market Data Adapter` (Binance, read-only) + zapis obserwacji do bazy. Potem brama **M3.5** (walidacja edge) przed M4.
```
