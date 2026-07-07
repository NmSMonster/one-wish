# PROTOKÓŁ WALIDACJI One Wish — kryteria zaliczenia zdefiniowane PRZED testem

> Zasada: kryteria akceptacji ustala się ZANIM test ruszy — inaczej każdy wynik
> da się zracjonalizować. Poniżej pełna ścieżka od sandboxa do pilota na realnych
> pieniądzach, etap po etapie, z twardymi progami PASS/FAIL. Etap N+1 wolno
> zacząć wyłącznie po PASS etapu N.

## S1 — Sandbox (automat, każdy push) ✅ ZALICZONE

| Kryterium | Próg | Stan |
|---|---|---|
| Pełna suita pytest | 100% zielona | ✅ 374/374 |
| Lint (ruff E/W/F) | 0 błędów | ✅ |
| Fuzz inwariantu (90 ziaren chaosu) | zero cichych orphanów | ✅ |
| Dryf księgowania vs księga dokładna | względny < 1e-9 | ✅ |
| E2E WS na symulatorze giełdy | connect+reconnect+trade | ✅ |
| CI (GitHub Actions, py3.11+3.12) | zielone na push | ✅ |

## S2 — Paper-live na żywych danych (maszyna właściciela, ≥ 48 h)

Uruchomienie (albo dwuklik `start_live.bat`):
```
python scripts/run_paper_live.py --mode live --budget-pln 150 --funding-weighted --transport ws
```

**PASS wymaga WSZYSTKICH poniższych:**

| # | Kryterium | Próg |
|---|---|---|
| 2.1 | Sesja przeżywa ≥ 48 h bez crashu procesu | 0 crashy (restart z recovery NIE dyskwalifikuje, ale liczymy go w 2.2) |
| 2.2 | Reconnecty WS / restarty | działają automatycznie; po każdym: pozycje odzyskane (log `RECOVERY`), zero rozjazdu księgi |
| 2.3 | Świeżość danych | mediana `data_lag_ms` < 2000; zero EMERGENCY_STOP od heartbeatu przy działającej sieci |
| 2.4 | Wejścia zgodne z regułami | każde wejście: funding ≥ próg, payback ≤ 30 rozliczeń, depth OK (audyt: `AuditTrail.explain`) |
| 2.5 | Delta-neutralność | `abs(net_delta)` każdej pary < 0.1% nominału przez całą sesję |
| 2.6 | Funding zebrany vs model | `FundingReconciler`: rozjazd < 10% względnie (paper: model vs model przez rozliczenia — sanity) |
| 2.7 | Budżet | ekspozycja nigdy > cap z budżetu (raport: utilization ≤ 100%) |
| 2.8 | PnL netto | raport pokazuje rozkład (realized/unrealized/funding/fees); **kierunek PnL NIE jest kryterium 48 h** — za krótko; kryterium to POPRAWNOŚĆ rozkładu (funding > 0 przy dodatnich stawkach, fees zgodne z liczbą wejść) |

**FAIL któregokolwiek → poprawka → powtórka S2 od zera.**

## S3 — Werdykt pomiarowy (jednorazowo, maszyna właściciela)

```
python scripts/study_funding.py     # aktualny carry (Tier A) na realnej historii
python scripts/study_venues.py     # uplift multi-venue (Tier B) → VENUE_SCAN.md
```

| Decyzja | Próg |
|---|---|
| Tier A dalej opłacalny | walk-forward OOS na kapitale > 8%/rok na wybranym koszyku (inaczej: bot czeka, nie handluje — carry to reżim, nie obietnica) |
| Tier B budować | uplift ≥ +5 pp/rok BRUTTO na ≥ 2 aktywach (inaczej: Binance-only, Tier B zamknięty świadomie) |

## S4 — Testnet (fałszywe pieniądze, klucze właściciela)

```
python scripts/run_testnet_smoke.py --asset BTC --qty 0.001 --price <cena> --max-notional 200 --yes
```

| # | Kryterium |
|---|---|
| 4.1 | Obie nogi FILLED, parsowanie filli poprawne (ceny/ilości niezerowe) |
| 4.2 | `reconcile()` po smoke: zero wiszących zleceń |
| 4.3 | `query_order` odnajduje złożone zlecenie po coid (ścieżka lost-ack realnie działa) |
| 4.4 | Zamknięcie perp z `reduceOnly` przechodzi (konto one-way i hedge-mode) |

## S5 — Pilot live (REALNE pieniądze, minimalna stawka)

Warunki wejścia: PASS S1–S4 + świadoma decyzja właściciela (`allow_mainnet=True`,
token uzbrojenia). Stawka startowa: minimalna możliwa (1 para, najtańszy alt
spełniający minNotional). Kryteria pierwszego tygodnia:

| # | Kryterium |
|---|---|
| 5.1 | Zero interwencji ręcznych wymuszonych błędem bota (interwencja z wyboru — OK) |
| 5.2 | `FundingReconciler`: realny income vs model < 15% rozjazdu względnego |
| 5.3 | Realny poślizg (CostTelemetry) ≤ 2× założenia CostModel — inaczej koszty w modelu są za optymistyczne i wracamy do S3 |
| 5.4 | Każdy alert CRITICAL ma wpis w dzienniku operatora z przyczyną i reakcją |

---

**Interpretacja całości:** "najlepszy na rynku / poziom miliardowej firmy" to nie
deklaracja — to przejście S1→S5 z zapisanymi wynikami. S1 jest zaliczone i
automatycznie pilnowane. S2–S5 wymagają żywego rynku i decyzji właściciela;
każdy etap ma jedną komendę i jednoznaczne progi, więc wynik nie podlega
interpretacji.
