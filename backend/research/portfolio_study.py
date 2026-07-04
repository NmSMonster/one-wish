"""Walidator portfelowy Tier A — równe wagi vs top-N vs ważenie funding, z ryzykiem.

Po co: sam mechanizm ważenia (FundingWeightedSizer) to obietnica, nie pomiar.
Ten moduł liczy na historii funding, ile NAPRAWDĘ dałaby dana alokacja — i za jaką
cenę ryzyka (max drawdown, Calmar, najgorsze pojedyncze rozliczenie). Dopiero taki
wynik wolno wpisać do CARRY_VERDICT.md jako nową liczbę.

Uczciwość konstrukcji:
- wagi liczy TA SAMA funkcja, której bot używa produkcyjnie
  (`FundingWeightedSizer.weight`) — badanie i egzekucja nie mogą się rozjechać,
- polityka per aktywo = smoothed (identyczna z `simulate_carry_smoothed`:
  wejście/wyjście po średniej kroczącej funding, fee za każdy cykl) — parytet
  z dotychczasowym werdyktem jest testowany co do bps,
- histories wyrównane od OGONA (wspólne ostatnie N rozliczeń) — bez sztucznego
  dosztukowywania danych aktywom o krótszej historii,
- wagi statyczne od wejścia (carry-hold nie rebalansuje — tak jak bot), portfel
  = kombinacja ważona krzywych per aktywo.

Ograniczenia (jak cały funding-study): pomija konwergencję basis i poślizg;
funding to dominujący, ale nie jedyny składnik PnL.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from statistics import fmean

from ..strategy.sizing import FundingWeightedSizer

_SETTLE_PER_YEAR = 3 * 365  # funding co 8h


@dataclass
class PortfolioResult:
    label: str
    weights: dict[str, float]        # znormalizowane (suma = 1)
    n_periods: int                   # wspólna długość historii (rozliczenia)
    final_net_bps: float             # skumulowany wynik netto portfela (bps kapitału)
    annualized_net_pct: float
    max_drawdown_pct: float          # najgłębsze obsunięcie krzywej (w % kapitału)
    calmar: float | None             # annualized / maxDD; None gdy maxDD == 0
    worst_settlement_bps: float      # najgorsze pojedyncze rozliczenie portfela


def smoothed_equity_curve(rates: list[float], round_trip_fee_bps: float = 18.6,
                          window: int = 9) -> list[float]:
    """Skumulowany wynik netto (bps) po każdym rozliczeniu dla polityki smoothed —
    ta sama logika co `simulate_carry_smoothed`, ale z pełną krzywą zamiast samej
    sumy (krzywa jest potrzebna do drawdownu). Parytet końcowej wartości z
    simulate_carry_smoothed jest pilnowany testem."""
    in_pos = False
    net = 0.0
    buf: deque = deque(maxlen=window)
    curve: list[float] = []
    for r in rates:
        bps = r * 1e4
        buf.append(bps)
        smoothed = sum(buf) / len(buf)
        if not in_pos:
            if smoothed > 0:
                in_pos = True
                net += bps - round_trip_fee_bps   # wejście: fee za cykl + pierwszy funding
        else:
            net += bps                            # trzymaj przez szum (też chwilowy minus)
            if smoothed <= 0:
                in_pos = False
        curve.append(net)
    return curve


def align_tail(rates_by_asset: dict[str, list[float]]) -> dict[str, list[float]]:
    """Przycina wszystkie historie do wspólnych OSTATNICH N rozliczeń (min długość).
    Ogon, nie początek: porównujemy aktywa w tym samym oknie czasu, a świeższe dane
    lepiej opisują obecny reżim. Aktywa z pustą historią odpadają."""
    filled = {k: v for k, v in rates_by_asset.items() if v}
    if not filled:
        return {}
    n = min(len(v) for v in filled.values())
    return {k: v[-n:] for k, v in filled.items()}


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Zostawia tylko dodatnie wagi i normalizuje do sumy 1 (pełne wdrożenie
    kapitału w wybrane aktywa)."""
    pos = {k: w for k, w in weights.items() if w > 0}
    total = sum(pos.values())
    if total <= 0:
        raise ValueError("brak dodatnich wag — portfel pusty")
    return {k: w / total for k, w in pos.items()}


def equal_weights(names: list[str]) -> dict[str, float]:
    if not names:
        raise ValueError("brak aktywów")
    return {n: 1.0 / len(names) for n in names}


def funding_weights(mean_funding_bps: dict[str, float], *, ref_funding_bps: float = 1.5,
                    min_mult: float = 0.5, max_mult: float = 2.0) -> dict[str, float]:
    """Wagi ∝ średniemu funding — liczone PRODUKCYJNĄ funkcją wagi bota
    (FundingWeightedSizer.weight), znormalizowane do sumy 1."""
    sizer = FundingWeightedSizer(base_notional_usd=1.0, ref_funding_bps=ref_funding_bps,
                                 min_mult=min_mult, max_mult=max_mult)
    return normalize_weights({k: sizer.weight(v) for k, v in mean_funding_bps.items()})


def top_n_assets(metric_by_asset: dict[str, float], n: int) -> list[str]:
    """Nazwy n aktywów o najwyższej metryce (np. smoothed net %/rok)."""
    return [k for k, _ in sorted(metric_by_asset.items(), key=lambda kv: -kv[1])[:n]]


def _drawdown(curve: list[float]) -> float:
    """Najgłębsze obsunięcie krzywej skumulowanej (bps). Kapitał startowy = 0 bps,
    więc szczyt startowy to max(0, ...) — obsunięcie od zera też jest stratą."""
    peak = 0.0
    worst = 0.0
    for v in curve:
        peak = max(peak, v)
        worst = max(worst, peak - v)
    return worst


def simulate_portfolio(rates_by_asset: dict[str, list[float]], weights: dict[str, float],
                       *, round_trip_fee_bps: float = 18.6, window: int = 9,
                       settle_per_year: float = _SETTLE_PER_YEAR,
                       label: str = "portfolio") -> PortfolioResult:
    """Portfel = ważona kombinacja krzywych smoothed-carry per aktywo (wagi statyczne
    — carry-hold nie rebalansuje). Zwraca zwrot, drawdown, Calmar i najgorsze
    pojedyncze rozliczenie. `settle_per_year` MUSI odpowiadać interwałowi funding
    aktywów (8h→3×365, 4h→6×365) — inaczej annualizacja jest błędna. Mieszaj tylko
    aktywa o TYM SAMYM interwale."""
    weights = normalize_weights(weights)
    aligned = align_tail({k: v for k, v in rates_by_asset.items() if k in weights})
    missing = set(weights) - set(aligned)
    if missing:
        raise ValueError(f"brak historii funding dla: {sorted(missing)}")
    n = min(len(v) for v in aligned.values())
    if n == 0:
        raise ValueError("pusta wspólna historia")

    curves = {k: smoothed_equity_curve(v, round_trip_fee_bps, window) for k, v in aligned.items()}
    portfolio = [sum(weights[k] * curves[k][t] for k in weights) for t in range(n)]

    final = portfolio[-1]
    per_settlement = [portfolio[0]] + [portfolio[t] - portfolio[t - 1] for t in range(1, n)]
    max_dd_bps = _drawdown(portfolio)
    years = n / settle_per_year
    annualized = (final / 100.0) / years if years > 0 else 0.0
    max_dd_pct = max_dd_bps / 100.0
    calmar = (annualized / max_dd_pct) if max_dd_pct > 0 else None

    return PortfolioResult(
        label=label,
        weights=weights,
        n_periods=n,
        final_net_bps=final,
        annualized_net_pct=annualized,
        max_drawdown_pct=max_dd_pct,
        calmar=calmar,
        worst_settlement_bps=min(per_settlement),
    )


def return_on_capital(annualized_notional_pct: float, perp_leverage: float) -> float:
    """Przelicza zwrot z NOMINAŁU pary na zwrot z KAPITAŁU właściciela.

    Funding nalicza się od nominału N, ale kapitał związany w parze to
    N (spot) + N/dźwignia (depozyt perp) = N·(1+1/lev). Stąd
    ROC = zwrot_nominalny × lev/(lev+1) — przy 3x to ×0.75. Kwotowane w werdyktach
    ~18% jest per-notional; właściciela interesuje TA liczba."""
    if perp_leverage <= 0:
        return 0.0
    return annualized_notional_pct * perp_leverage / (perp_leverage + 1.0)


# -- walk-forward: selekcja out-of-sample (bez look-ahead) ------------------- #
@dataclass
class WalkForwardWindow:
    train_start: int
    test_start: int
    test_end: int
    weights: dict[str, float]        # skład wybrany NA TRENINGU ({} = okno bez handlu)
    net_bps: float                   # wynik na oknie TESTOWYM
    annualized_net_pct: float


@dataclass
class WalkForwardResult:
    label: str
    train: int
    test: int
    windows: list[WalkForwardWindow] = field(default_factory=list)
    total_net_bps: float = 0.0
    n_test_periods: int = 0
    settle_per_year: float = _SETTLE_PER_YEAR

    @property
    def annualized_net_pct(self) -> float:
        years = self.n_test_periods / self.settle_per_year
        return (self.total_net_bps / 100.0) / years if years > 0 else 0.0


def walk_forward(rates_by_asset: dict[str, list[float]], *, train: int, test: int,
                 round_trip_fee_bps: float = 18.6, window: int = 9,
                 top_n: int | None = None, funding_weighted: bool = True,
                 ref_funding_bps: float = 1.5, settle_per_year: float = _SETTLE_PER_YEAR,
                 label: str = "walk-forward") -> WalkForwardResult:
    """Uczciwa (out-of-sample) wersja werdyktu portfelowego: skład i wagi wybierane
    WYŁĄCZNIE na oknie treningowym (`train` rozliczeń), wynik mierzony na NASTĘPNYM
    oknie (`test` rozliczeń), okno przesuwa się o `test`. Eliminuje look-ahead bias
    selekcji top-N/wag (in-sample wybiera zwycięzców z perspektywy czasu).

    Konserwatywnie: każde okno testowe rusza „na zimno" (świeże wejście = świeże
    round-trip fee per aktywo) — wynik OOS jest raczej zaniżony niż zawyżony.
    Okna, w których trening nie wskazał żadnego aktywa z dodatnim funding, nie
    handlują (wkład 0, uczciwie liczone do czasu)."""
    aligned = align_tail(rates_by_asset)
    if not aligned:
        raise ValueError("brak historii funding")
    n = min(len(v) for v in aligned.values())
    if n < train + test:
        raise ValueError(f"historia za krótka: {n} < train+test = {train + test}")

    result = WalkForwardResult(label=label, train=train, test=test, settle_per_year=settle_per_year)
    for start in range(train, n - test + 1, test):
        train_slices = {k: v[start - train:start] for k, v in aligned.items()}
        test_slices = {k: v[start:start + test] for k, v in aligned.items()}

        mean_bps = {k: fmean(v) * 1e4 for k, v in train_slices.items()}
        candidates = {k: m for k, m in mean_bps.items() if m > 0}
        if top_n is not None and candidates:
            keep = top_n_assets(candidates, top_n)
            candidates = {k: candidates[k] for k in keep}

        if not candidates:
            result.windows.append(WalkForwardWindow(start - train, start, start + test, {}, 0.0, 0.0))
            result.n_test_periods += test
            continue

        weights = (funding_weights(candidates, ref_funding_bps=ref_funding_bps)
                   if funding_weighted else equal_weights(list(candidates)))
        res = simulate_portfolio(test_slices, weights, round_trip_fee_bps=round_trip_fee_bps,
                                 window=window, settle_per_year=settle_per_year)
        result.windows.append(WalkForwardWindow(start - train, start, start + test,
                                                res.weights, res.final_net_bps,
                                                res.annualized_net_pct))
        result.total_net_bps += res.final_net_bps
        result.n_test_periods += test
    return result
