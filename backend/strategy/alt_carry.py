"""Doradca BEZPIECZNYCH wejść w carry na wysoko-funding altach (VELVET/TAC/...).

Kodyfikuje wnioski z całej analizy (funding OOS + cross-margin + ryzyko likwidacji
+ płynność) w twarde, testowalne reguły. To NIE otwiera pozycji — daje werdykt
per alt: wchodzić czy nie, jaką dźwignią, jakim maks. nominałem, z jakimi wymogami.

Zasady bezpieczeństwa (wywalczone realnymi danymi):
1. **Cross/portfolio margin OBOWIĄZKOWY.** Isolated short na alcie = pewna likwidacja
   przy pierwszym pumpie (VELVET/TAC robiły +147/+287%). Cross: spot pokrywa perp.
2. **Min. funding OOS** — nie gonimy niskiego funding (HYPE ~3% OOS odpada); bierzemy
   tylko realny, walk-forward-owy zwrot.
3. **Bufor basis** — dźwignia dobrana tak, by perp mógł wystrzelić X% ponad spot i
   nadal przeżyć (zapas na short-squeeze). Za mały bufor → niższa dźwignia.
4. **Płynność wejścia I WYJŚCIA** — realne ryzyko to domknięcie dużego shorta w trakcie
   pumpa; wymagamy głębokości ≥ krotność nominału.
5. **Cap per alt** — koncentracja zabija: jeden depeg/delisting nie może zatopić konta.
   Nowe/cienkie alty dostają mniejszy cap.
6. **Min. historia** — świeże kontrakty (< próg dni) są ryzykowniejsze (mało danych,
   przeszły funding tym bardziej ≠ przyszły) → mniejszy cap lub wykluczenie.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..risk.margin import DeltaNeutralCrossStress


@dataclass
class AltCandidate:
    asset: str
    funding_oos_pct: float          # zwrot funding OOS na kapitale (walk-forward)
    worst_move_frac: float          # najgorszy historyczny ruch ceny w górę
    depth_usd: float                # płynność (top-of-book, USD)
    history_days: int


@dataclass
class AltEntryDecision:
    asset: str
    include: bool
    max_leverage: float
    max_notional_usd: float
    basis_buffer_frac: float        # zapas na squeeze przy wybranej dźwigni (cross)
    requires_cross_margin: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class AltCarryAdvisor:
    base_notional_usd: float
    min_funding_oos_pct: float = 8.0
    min_basis_buffer: float = 0.40          # perp musi móc wystrzelić ≥40% ponad spot
    min_depth_mult: float = 10.0            # głębokość ≥ 10× nominał (wejście+wyjście)
    min_history_days: int = 180
    leverage_options: tuple[float, ...] = (1.5, 2.0, 3.0)
    maintenance_margin_rate: float = 0.02
    spot_haircut: float = 0.10

    def _best_leverage(self, worst_move: float) -> tuple[float, float]:
        """Najwyższa dźwignia z `leverage_options`, przy której bufor basis (cross)
        pod najgorszym historycznym ruchem ≥ min_basis_buffer. Zwraca (dźwignia, bufor).
        Gdy żadna nie spełnia — najniższa dostępna (najbezpieczniejsza) + jej bufor."""
        best = None
        for lev in sorted(self.leverage_options):
            cross = DeltaNeutralCrossStress(perp_leverage=lev,
                                            maintenance_margin_rate=self.maintenance_margin_rate,
                                            spot_haircut=self.spot_haircut)
            buf = cross.max_basis_stress(worst_move)
            if buf >= self.min_basis_buffer:
                best = (lev, buf)              # spełnia próg → kandydat (bierzemy najwyższą taką)
        if best is not None:
            return best
        lo = min(self.leverage_options)
        cross = DeltaNeutralCrossStress(perp_leverage=lo,
                                        maintenance_margin_rate=self.maintenance_margin_rate,
                                        spot_haircut=self.spot_haircut)
        return lo, cross.max_basis_stress(worst_move)

    def evaluate(self, c: AltCandidate) -> AltEntryDecision:
        reasons: list[str] = []
        include = True

        if c.funding_oos_pct < self.min_funding_oos_pct:
            include = False
            reasons.append(f"funding OOS {c.funding_oos_pct:.1f}% < próg {self.min_funding_oos_pct:.0f}%")

        lev, buf = self._best_leverage(c.worst_move_frac)
        if buf < self.min_basis_buffer:
            include = False
            reasons.append(f"bufor basis {buf:.0%} < próg {self.min_basis_buffer:.0%} nawet przy min. dźwigni")

        # nominał: baza, ścinana za cienką płynność i krótką historię (koncentracja/nowość)
        notional = self.base_notional_usd
        if c.depth_usd < self.min_depth_mult * notional:
            # utnij nominał do tego, co płynność bezpiecznie udźwignie (wejście+wyjście)
            safe = c.depth_usd / self.min_depth_mult
            if safe < 1.0:
                include = False
                reasons.append("płynność zbyt niska na jakikolwiek bezpieczny nominał")
            else:
                notional = min(notional, safe)
                reasons.append(f"nominał ścięty do {notional:.0f}$ przez płynność")

        if c.history_days < self.min_history_days:
            include = False
            reasons.append(f"historia {c.history_days}d < próg {self.min_history_days}d (świeży kontrakt)")

        if include and not reasons:
            reasons.append(f"OK: funding {c.funding_oos_pct:.1f}% OOS, dźwignia {lev:.1f}x, bufor {buf:.0%}")

        return AltEntryDecision(
            asset=c.asset, include=include, max_leverage=lev,
            max_notional_usd=(notional if include else 0.0),
            basis_buffer_frac=buf, requires_cross_margin=True, reasons=reasons)

    def evaluate_all(self, candidates: list[AltCandidate]) -> list[AltEntryDecision]:
        return [self.evaluate(c) for c in candidates]
