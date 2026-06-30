"""PositionBook — księgowanie pozycji, fillów i PnL.

Każda noga (spot/perp) trzyma wielkość ze znakiem i średnią cenę wejścia.
Przy redukcji pozycji realizujemy PnL; przy zwiększaniu uśredniamy wejście.
Dla pary delta-neutral (long spot + short perp) ruch ceny na obu nogach znosi
się — zysk pochodzi z konwergencji basis i funding, nie z kierunku.
"""
from __future__ import annotations

from ..core.types import Asset, Fill, Leg, PnLSnapshot, Position, Side


def apply_to_leg(qty: float, entry: float, side: Side, price: float,
                 fill_qty: float) -> tuple[float, float, float]:
    """Aktualizuje (wielkość, średnie wejście) nogi i zwraca zrealizowany PnL."""
    d = fill_qty if side == Side.BUY else -fill_qty
    realized = 0.0

    if qty == 0 or (qty > 0) == (d > 0):
        # otwieranie / zwiększanie tej samej strony → uśrednij wejście
        new_qty = qty + d
        entry = price if qty == 0 else (entry * qty + price * d) / new_qty
        qty = new_qty
    else:
        # redukcja → realizuj PnL na zamykanej części
        closing = min(abs(d), abs(qty))
        realized = (price - entry) * closing if qty > 0 else (entry - price) * closing
        new_qty = qty + d
        if abs(d) > abs(qty):      # przejście na drugą stronę → reszta otwiera nową
            entry = price
        qty = new_qty

    return qty, entry, realized


class PositionBook:
    def __init__(self) -> None:
        self.positions: dict[Asset, Position] = {}
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.funding_collected = 0.0
        self._counter = 0

    def is_open(self, asset: Asset) -> bool:
        p = self.positions.get(asset)
        return bool(p and p.is_open)

    def position(self, asset: Asset) -> Position | None:
        return self.positions.get(asset)

    def apply_fill(self, fill: Fill, ts: float) -> float:
        p = self.positions.get(fill.asset)
        if p is None or not p.is_open:
            self._counter += 1
            p = Position(id=f"pos-{fill.asset.value}-{self._counter}",
                         asset=fill.asset, opened_ts=ts)
            self.positions[fill.asset] = p

        if fill.leg == Leg.SPOT:
            p.spot_qty, p.spot_entry, r = apply_to_leg(
                p.spot_qty, p.spot_entry, fill.side, fill.price, fill.qty)
        else:
            p.perp_qty, p.perp_entry, r = apply_to_leg(
                p.perp_qty, p.perp_entry, fill.side, fill.price, fill.qty)

        self.realized_pnl += r
        self.fees_paid += fill.fee
        p.realized += r
        p.fees += fill.fee
        if not p.is_open:
            p.closed_ts = ts
        return r

    def add_funding(self, asset: Asset, amount: float) -> None:
        p = self.positions.get(asset)
        if p and p.is_open:
            p.funding_accrued += amount
        self.funding_collected += amount

    def snapshot(self, marks: dict[Asset, tuple[float, float]], ts: float) -> PnLSnapshot:
        unrealized = 0.0
        for asset, p in self.positions.items():
            if p.is_open and asset in marks:
                unrealized += p.unrealized_pnl(marks[asset][0], marks[asset][1])
        return PnLSnapshot(
            ts=ts,
            realized=self.realized_pnl,
            unrealized=unrealized,
            funding_collected=self.funding_collected,
            fees_paid=self.fees_paid,
        )
