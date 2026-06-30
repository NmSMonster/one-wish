"""Budżet kapitału — forward paper-trade na żywym rynku z fikcyjnym kapitałem.

Cel: puścić bota na ŻYWYCH danych Binance z egzekucją PAPIEROWĄ i fikcyjnym
budżetem (np. ~150 zł), żeby ocenić zachowanie BEZ ryzyka pieniędzy. To NIE jest
realny handel — tylko ograniczenie i tracking kapitału na potrzeby uczciwego testu.

Kapitał związany w parze delta-neutral = pełny nominał nogi spot + depozyt nogi
short perp (nominał / dźwignia). Stąd budżet B utrzyma łączny nominał do
B / (1 + 1/dźwignia).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..execution.book import PositionBook
from ..risk.manager import RiskConfig

# Przybliżony kurs (USDT ≈ USD). Aktualizowany ręcznie; do fikcyjnego budżetu w PLN
# nie potrzebujemy precyzji co do grosza — to test zachowania, nie księgowość.
USD_PER_PLN = 0.25     # ~1 USD = 4 PLN


def pln_to_usd(pln: float) -> float:
    return pln * USD_PER_PLN


def budget_risk_config(budget_usd: float, *, perp_leverage: float = 3.0,
                       base: RiskConfig | None = None, **overrides) -> RiskConfig:
    """RiskConfig z limitami ekspozycji ograniczonymi do budżetu.

    Łączny nominał (sumowany przez RiskManagera jako `exposure`) jest ograniczony do
    budżetu po uwzględnieniu, że perp wymaga tylko depozytu: cap = B / (1 + 1/lev).
    Konserwatywnie ten sam cap dla pojedynczej transakcji i aktywa (przy małym
    budżecie i tak zmieści się ~1 para). `overrides` pozwalają dostroić limity.
    """
    base = base or RiskConfig()
    cap = budget_usd / (1.0 + 1.0 / perp_leverage) if perp_leverage > 0 else budget_usd
    cfg = replace(
        base,
        perp_leverage=perp_leverage,
        max_total_exposure_usd=cap,
        max_asset_exposure_usd=cap,
        max_trade_notional_usd=cap,
    )
    return replace(cfg, **overrides) if overrides else cfg


@dataclass
class BudgetTracker:
    """Śledzi, ile z fikcyjnego budżetu jest związane w otwartych pozycjach i ile
    zostało wolne ("ile z 150 zł zostało")."""
    budget_usd: float
    perp_leverage: float = 3.0

    def committed(self, book: PositionBook) -> float:
        """Kapitał związany w otwartych parach: nominał spot + depozyt perp."""
        total = 0.0
        for _, p in book.positions.items():
            if not p.is_open:
                continue
            spot_notional = abs(p.spot_qty) * p.spot_entry
            perp_notional = abs(p.perp_qty) * p.perp_entry
            total += spot_notional + perp_notional / self.perp_leverage
        return total

    def free(self, book: PositionBook) -> float:
        return max(0.0, self.budget_usd - self.committed(book))

    def utilization(self, book: PositionBook) -> float:
        return self.committed(book) / self.budget_usd if self.budget_usd > 0 else 0.0

    def can_open(self, notional_usd: float, book: PositionBook,
                 *, perp_leverage: float | None = None) -> bool:
        """Czy nowa para o danym nominale zmieści się w wolnym budżecie."""
        lev = perp_leverage or self.perp_leverage
        cost = notional_usd * (1.0 + 1.0 / lev) if lev > 0 else notional_usd
        return cost <= self.free(book) + 1e-9

    def snapshot(self, book: PositionBook) -> dict:
        committed = self.committed(book)
        return {
            "budget_usd": self.budget_usd,
            "committed_usd": committed,
            "free_usd": max(0.0, self.budget_usd - committed),
            "utilization_pct": (committed / self.budget_usd * 100.0) if self.budget_usd > 0 else 0.0,
        }
