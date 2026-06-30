"""Raport dzienny — podsumowanie sesji paper-live (M11)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..execution import PositionBook
from ..storage import AuditTrail, Database


@dataclass
class DailyReport:
    counts: dict = field(default_factory=dict)
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    net: float = 0.0
    open_positions: int = 0

    def summary(self) -> str:
        c = self.counts
        return "\n".join([
            "=== RAPORT DZIENNY ===",
            f"ticki:             {c.get('MARKET_TICK', 0)}",
            f"sygnały EDGE:      {c.get('EDGE_DETECTED', 0)}",
            f"wejścia:           {c.get('POSITION_OPENED', 0)}",
            f"wyjścia:           {c.get('POSITION_CLOSED', 0)}",
            f"odrzucenia ryzyka: {c.get('RISK_REJECTED', 0)}",
            f"fille:             {c.get('FILL', 0)}",
            f"awaryjne stopy:    {c.get('EMERGENCY_STOP', 0)}",
            f"otwarte pozycje:   {self.open_positions}",
            f"PnL zrealizowany:  {self.realized:+.2f}$",
            f"prowizje:          {self.fees:.2f}$",
            f"funding:           {self.funding:+.2f}$",
            f"PnL NETTO:         {self.net:+.2f}$",
        ])


def build_report(db: Database, book: PositionBook) -> DailyReport:
    counts = AuditTrail(db).summary()
    net = book.realized_pnl - book.fees_paid + book.funding_collected
    open_positions = sum(1 for p in book.positions.values() if p.is_open)
    return DailyReport(
        counts=counts,
        realized=book.realized_pnl,
        fees=book.fees_paid,
        funding=book.funding_collected,
        net=net,
        open_positions=open_positions,
    )
