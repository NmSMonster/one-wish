# RYZYKO LIKWIDACJI — realna historia cen

> Wygenerowane 2026-07-04 przez `scripts/study_alt_risk.py`. Interwał 1d,
> okno reakcji 1 bar, maintenance margin 2%.

```
asset   bary  najg.1bar  najg.okno  likwidacja? [3x 4x 5x]
----------------------------------------------------------
```

Progi likwidacji (ruch ceny w górę): 3x → +31%, 4x → +23%, 5x → +18%

## Jak czytać

- **najg.okno** = największy wzrost ceny w oknie reakcji (dla shorta = maksymalna strata).
- **LIKW** przy dźwigni = ten historyczny ruch PRZEKROCZYŁ próg likwidacji → short zostałby
  zlikwidowany po złej cenie. Jeden taki ruch kasuje miesiące dochodu z funding.
- Wysoki funding altów (VELVET/TAC ~29%/rok) jest ZAPŁATĄ za tę zmienność. To NIE jest
  darmowy pieniądz — funding-study widzi nagrodę, ten raport widzi ryzyko.

## Wniosek operacyjny

Jeśli alt pokazuje LIKW przy Twojej dźwigni → wejście w niego tą dźwignią to zakład,
że przyszłość będzie spokojniejsza niż przeszłość. Bezpieczne wejście wymaga: niższej
dźwigni (próg likwidacji dalej), mniejszego kapitału per alt, i twardego MarginWatchdoga
domykającego short ZANIM dojdzie do likwidacji.
