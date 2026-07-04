"""Ocena ryzyka LIKWIDACJI nogi short — ryzyko, którego funding-study nie widzi.

Funding-study mierzy nagrodę (dochód z funding), ale jest ślepy na to, co naprawdę
zabija delta-neutral carry na zmiennych altach: gwałtowny wzrost ceny, który
likwiduje nogę short zanim zdążymy ją domknąć. Wysoki funding (VELVET/TAC ~29%/rok)
jest ZAPŁATĄ za tę zmienność — ta sama zmienność, która płaci, potrafi zlikwidować.

Model (isolated, short): likwidacja gdy strata zje depozyt, czyli przy ruchu ceny
w GÓRĘ o ~ (1/dźwignia − maintenance_margin_rate). Przy 3x i mmr 2% to ~+31.3%.
Sprawdzamy, czy realna historia CEN kiedykolwiek zrobiła taki ruch w oknie krótszym
niż zdążylibyśmy zareagować — jeśli tak, jeden taki ruch kasuje miesiące funding.
"""
from __future__ import annotations

from dataclasses import dataclass


def liquidation_move_frac(leverage: float, mmr: float) -> float:
    """Ruch ceny w górę (frakcja), przy którym short (isolated) jest likwidowany.
    3x, mmr 0.02 → 1/3 − 0.02 = 0.313 (+31.3%)."""
    if leverage <= 0:
        return float("inf")
    return 1.0 / leverage - mmr


def max_run_up(prices: list[float], window: int) -> float:
    """Największy wzrost ceny (frakcja) od dowolnego baru do maksimum w kolejnych
    `window` barach — maksymalna strata dla shorta trzymanego przez to okno.
    `prices` powinny być cenami HIGH (konserwatywnie, bo intra-bar szczyt likwiduje)."""
    worst = 0.0
    n = len(prices)
    for i in range(n):
        base = prices[i]
        if base <= 0:
            continue
        hi = max(prices[i:i + window + 1])
        worst = max(worst, hi / base - 1.0)
    return worst


def worst_intrabar_up(highs: list[float], opens: list[float]) -> float:
    """Największy skok WEWNĄTRZ pojedynczego bara: high/open − 1. To ryzyko gapu/
    świecowego wystrzału (short może dostać margin call w minuty, nie w dni)."""
    worst = 0.0
    for h, o in zip(highs, opens):
        if o > 0:
            worst = max(worst, h / o - 1.0)
    return worst


@dataclass
class LiquidationAssessment:
    symbol: str
    n_bars: int
    interval: str
    worst_1bar_up: float             # największy skok wewnątrz jednego bara (high/open−1)
    worst_window_up: float           # najgorszy ruch w górę w oknie reakcji (frakcja)
    window: int
    breaches: dict                   # dźwignia -> czy historyczny ruch przekroczył próg likwidacji (isolated)


def assess_short_liquidation(highs: list[float], opens: list[float] | None = None, *,
                             interval: str = "1d", window: int = 3,
                             leverages: tuple[float, ...] = (3.0, 4.0, 5.0),
                             mmr: float = 0.02, symbol: str = "") -> LiquidationAssessment:
    """Ocena (ISOLATED margin — najgorszy przypadek): czy realna historia CEN
    zlikwidowałaby short przy danych dźwigniach. `window` = ile barów zajęłaby
    reakcja/domknięcie (konserwatywnie ≥1). `breaches` dotyczy isolated; w cross/
    portfolio margin spot pokrywa perp — patrz `DeltaNeutralCrossStress`."""
    w1 = worst_intrabar_up(highs, opens) if opens else 0.0
    ww = max_run_up(highs, window)
    breaches = {}
    for lev in leverages:
        thr = liquidation_move_frac(lev, mmr)
        breaches[lev] = {"threshold": thr, "liquidated": ww >= thr}
    return LiquidationAssessment(
        symbol=symbol, n_bars=len(highs), interval=interval,
        worst_1bar_up=w1, worst_window_up=ww, window=window, breaches=breaches)
