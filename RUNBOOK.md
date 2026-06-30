# RUNBOOK operatora — One Wish

Krótki przewodnik reagowania na alerty. One Wish to bot carry delta-neutral
(long spot + short perp). Najgroźniejszy tryb porażki to **rozjechanie hedge'a**
(orphan leg) albo **likwidacja nogi short-perp**. System sam się broni
(MarginWatchdog, CircuitBreaker, OrderManager, Monitor), a alerty informują
operatora, gdy dzieje się coś istotnego.

## Kanały alertów

Alerty idą przez `AlertManager` (backend/monitoring/alerts.py) do skonfigurowanych
sinków. Konfiguracja **wyłącznie z env** (sekrety nigdy w repo):

```
ONEWISH_ALERT_DISCORD_WEBHOOK = https://discord.com/api/webhooks/...
ONEWISH_ALERT_TELEGRAM_TOKEN  = <bot token>
ONEWISH_ALERT_TELEGRAM_CHAT   = <chat id>
```

Bez konfiguracji alerty trafiają tylko do logu (`onewish.alerts`). Próg domyślny:
WARNING. Throttling per (rodzaj, aktywo); **CRITICAL nigdy nie jest tłumiony**.

## Tabela alertów → reakcja

| Alert | Severity | Co znaczy | Reakcja operatora |
|---|---|---|---|
| **EMERGENCY STOP** | CRITICAL | Monitor zatrzymał bota (dzienny limit straty albo zamrożony feed). Ryzyko killnięte, pozycje domykane. | Zweryfikuj przyczynę w logu/raporcie. Nie restartuj na ślepo — najpierw ustal, czy strata realna czy z błędu danych. |
| **KILL SWITCH** | CRITICAL | Ręczny kill (operator/GUI). | Świadoma akcja — potwierdź, że to Ty/zespół. Jeśli nie, traktuj jak incydent bezpieczeństwa. |
| **Margin flatten** | CRITICAL | Zdrowie marginu nogi short spadło ≤ próg flatten — kontrolowane domknięcie PRZED likwidacją. | Sprawdź, czy para domknęła się czysto (reconcile). Rozważ niższą dźwignię (`perp_leverage`) na przyszłość. |
| **Margin warn** | WARNING | Zdrowie marginu w strefie ostrzegawczej (jeszcze bezpiecznie, ale blisko). | Obserwuj. Jeśli perp dalej rośnie — przygotuj się na flatten. Rozważ stress-test (`MarginStressTester`). |
| **Risk limit circuit_open** | CRITICAL | CircuitBreaker wykrył anomalię (skok premii/OI, płytki rynek, fala likwidacji) — wstrzymuje wejścia na aktywie. | To ochrona, nie błąd. Sprawdź rynek danego aktywa. Wejścia wrócą po `circuit_close`. |
| **Stale feed** | WARNING→stop | Luka w strumieniu danych = nie ufamy rynkowi. Monitor eskaluje do EMERGENCY STOP. | Sprawdź łączność/źródło danych. Bot nie handluje na nieświeżych danych — to celowe. |
| **Data lag** | WARNING | Opóźnienie danych powyżej progu (ale feed żyje). | Zwykle przejściowe. Jeśli się utrzymuje → sprawdź sieć/proxy. |
| **Order rejected** | WARNING | Giełda odrzuciła zlecenie (transient/filtry). OrderManager retry + pilnuje inwariantu. | Pojedyncze: zignoruj. Powtarzalne na jednej nodze → ryzyko niepełnej pary; sprawdź reconcile i filtry symbolu. |

## Inwariant bezpieczeństwa (zawsze)

Po każdej próbie otwarcia/zamknięcia pozycja jest **albo delta-neutral, albo flat**
— nigdy orphan leg. Jeśli `reconcile()` raportuje `imbalanced` niepuste:

1. Nie panikuj — OrderManager kompensuje automatycznie (flatten).
2. Jeśli zostało po restarcie: uruchom kontrolowany flatten (`_flatten` / CLOSE intent).
3. Potwierdź flat: `reconcile()["imbalanced"] == []` i `book.is_open(asset) == False`.

## Stress-test marginu (przed/podczas trzymania)

```python
from backend.risk.margin import MarginStressTester
rep = MarginStressTester(book, perp_leverage=3.0).stress(marks)
rep["summary"]["safe"]            # czy przeżyje +10/20/30%
rep["summary"]["liquidation_at"]  # aktywa likwidowane przy danym szoku
```

Jeśli `safe == False` przy realnym szoku → obniż dźwignię lub zmniejsz pozycję.

## Złota zasada

Realny handel jest **domyślnie zablokowany** (`binance_live`, `transport_implemented
= False`). Każdy alert na produkcji w fazie paper = sygnał diagnostyczny, nie strata
pieniędzy. Nie odblokowuj live bez: testnet + pozytywny werdykt + świadoma decyzja.
