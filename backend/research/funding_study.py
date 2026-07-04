"""Analiza realnej historii funding — empiryczny werdykt carry.

Carry (long spot + short perp) zarabia na funding co 8h, płacąc prowizje raz na
wejście+wyjście. Mając realną historię stawek funding (Binance publikuje ~rok
wstecz) możemy ocenić, czy ta strategia miała przewagę — bez czekania na zbieranie
danych na żywo. To pierwszorzędny werdykt: pomija konwergencję basis i poślizg,
ale funding to dominujący sterownik carry.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import fmean, median

_SETTLE_PER_YEAR = 3 * 365  # funding co 8h


@dataclass
class FundingStats:
    n: int
    mean_bps: float
    median_bps: float
    pct_positive: float
    annualized_pct: float    # średni funding × liczba rozliczeń/rok × 100
    min_bps: float
    max_bps: float


@dataclass
class CarryResult:
    label: str
    funding_collected_bps: float
    fees_bps: float
    net_bps: float
    num_cycles: int          # liczba par wejście+wyjście (każda = round-trip fee)
    periods_held: int
    total_periods: int
    annualized_net_pct: float


def rates_from_history(history: list[dict]) -> list[float]:
    """Wyciąga stawki funding (decimal) z odpowiedzi /fapi/v1/fundingRate."""
    out = []
    for item in history:
        try:
            out.append(float(item["fundingRate"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


_FUNDING_PERIOD_MS = 8 * 3600 * 1000  # rozliczenie co 8h


def count_funding_gaps(history: list[dict], tol_frac: float = 0.25) -> dict:
    """Sprawdza spójność historii funding po `fundingTime`: ile rozliczeń brakuje
    (odstęp > 8h) i ile jest zduplikowanych/nietypowo bliskich (odstęp < 8h). Cienki
    edge carry jest wrażliwy na luki — jedno przegapione rozliczenie to realny bps.
    Zwraca liczbę brakujących rozliczeń (oszacowaną z rozmiaru luk) i anomalii."""
    times = []
    for item in history:
        try:
            times.append(int(item["fundingTime"]))
        except (KeyError, TypeError, ValueError):
            continue
    times.sort()
    tol = _FUNDING_PERIOD_MS * tol_frac
    missing = 0
    anomalies = 0
    for prev, cur in zip(times, times[1:]):
        gap = cur - prev
        if gap > _FUNDING_PERIOD_MS + tol:
            missing += round(gap / _FUNDING_PERIOD_MS) - 1
        elif gap < _FUNDING_PERIOD_MS - tol:
            anomalies += 1
    return {"n": len(times), "missing_settlements": missing, "irregular_gaps": anomalies}


def infer_settle_per_year(history: list[dict]) -> dict:
    """Wyprowadza REALNY interwał funding z medianowego odstępu `fundingTime`.
    Kluczowe dla poprawnej annualizacji: nowe alty (VELVET/TAC/HYPE) rozliczają się
    często co 4h albo 1h, nie 8h — annualizacja stałym 3×365 daje wtedy błędny wynik.
    Zwraca interwał w godzinach, liczbę rozliczeń/rok i czy to standardowe 8h."""
    times = sorted(int(item["fundingTime"]) for item in history
                   if str(item.get("fundingTime", "")).lstrip("-").isdigit())
    if len(times) < 3:
        return {"interval_hours": 8.0, "settle_per_year": float(_SETTLE_PER_YEAR),
                "standard_8h": True, "n": len(times)}
    gaps = sorted(cur - prev for prev, cur in zip(times, times[1:]) if cur > prev)
    median_ms = gaps[len(gaps) // 2]
    interval_hours = median_ms / 3_600_000.0
    settle_per_year = 8760.0 / interval_hours if interval_hours > 0 else float(_SETTLE_PER_YEAR)
    standard = abs(interval_hours - 8.0) <= 8.0 * 0.25   # 8h ±25%
    return {"interval_hours": interval_hours, "settle_per_year": settle_per_year,
            "standard_8h": standard, "n": len(times)}


def analyze_funding(rates: list[float], settle_per_year: float = _SETTLE_PER_YEAR) -> FundingStats:
    if not rates:
        return FundingStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    bps = [r * 1e4 for r in rates]
    return FundingStats(
        n=len(rates),
        mean_bps=fmean(bps),
        median_bps=median(bps),
        pct_positive=sum(1 for b in bps if b > 0) / len(bps) * 100.0,
        annualized_pct=fmean(rates) * settle_per_year * 100.0,
        min_bps=min(bps),
        max_bps=max(bps),
    )


def _annualize(net_bps: float, total_periods: int,
               settle_per_year: float = _SETTLE_PER_YEAR) -> float:
    if total_periods <= 0:
        return 0.0
    years = total_periods / settle_per_year
    return (net_bps / 100.0) / years if years > 0 else 0.0


def simulate_carry_always_in(rates: list[float], round_trip_fee_bps: float = 18.6,
                             settle_per_year: float = _SETTLE_PER_YEAR) -> CarryResult:
    """Najprostszy carry: wejście raz, trzymanie przez całą historię, wyjście raz.
    Inkasuje KAŻDY funding (też ujemny), płaci jeden round-trip."""
    funding = sum(r * 1e4 for r in rates)
    fees = round_trip_fee_bps if rates else 0.0
    net = funding - fees
    return CarryResult("always-in", funding, fees, net, 1 if rates else 0,
                       len(rates), len(rates), _annualize(net, len(rates), settle_per_year))


def simulate_carry_positive_only(rates: list[float], round_trip_fee_bps: float = 18.6,
                                 entry_threshold_bps: float = 0.0,
                                 settle_per_year: float = _SETTLE_PER_YEAR) -> CarryResult:
    """Wchodzi gdy funding > próg, trzyma dopóki dodatni, wychodzi gdy ≤ 0.
    Każdy cykl wejście+wyjście kosztuje jeden round-trip."""
    in_pos = False
    funding = 0.0
    fees = 0.0
    cycles = 0
    held = 0
    for r in rates:
        bps = r * 1e4
        if not in_pos:
            if bps > entry_threshold_bps:
                in_pos = True
                cycles += 1
                fees += round_trip_fee_bps
                funding += bps
                held += 1
        else:
            if bps > 0:
                funding += bps
                held += 1
            else:
                in_pos = False
    net = funding - fees
    return CarryResult("positive-only", funding, fees, net, cycles, held,
                       len(rates), _annualize(net, len(rates), settle_per_year))


def simulate_carry_smoothed(rates: list[float], round_trip_fee_bps: float = 18.6,
                            window: int = 9,
                            settle_per_year: float = _SETTLE_PER_YEAR) -> CarryResult:
    """Carry z wygładzonym sygnałem: wchodzi/wychodzi wg ŚREDNIEJ kroczącej funding
    (≈ window×8h reżimu), więc NIE churnuje na pojedynczym ujemnym funding. To
    właściwa polityka: trzymaj przez szum, wyjdź dopiero gdy reżim się odwróci."""
    in_pos = False
    funding = 0.0
    fees = 0.0
    cycles = 0
    held = 0
    buf: deque = deque(maxlen=window)
    for r in rates:
        bps = r * 1e4
        buf.append(bps)
        smoothed = sum(buf) / len(buf)
        if not in_pos:
            if smoothed > 0:
                in_pos = True
                cycles += 1
                fees += round_trip_fee_bps
                funding += bps
                held += 1
        else:
            funding += bps            # inkasuj cokolwiek (też chwilowy minus)
            held += 1
            if smoothed <= 0:
                in_pos = False
    net = funding - fees
    return CarryResult("smoothed", funding, fees, net, cycles, held,
                       len(rates), _annualize(net, len(rates), settle_per_year))
