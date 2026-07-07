"""Dryf księgowania float vs księga DOKŁADNA (Fraction) — dowód, nie deklaracja.

Pytanie z fazy realnego kapitału: czy float w PositionBook jest wystarczający,
czy trzeba Decimal? Zamiast wierzyć — mierzymy: tысiące losowych filli przechodzi
równolegle przez produkcyjną księgę (float) i przez referencję na ułamkach
dokładnych (fractions.Fraction, zero błędu zaokrągleń). Jeśli względny dryf
przekroczy tolerancję, ten test padnie i refaktor na Decimal będzie uzasadniony
twardym dowodem. Ceny/ilości losowane z siatek giełdowych (tick/step), jak w
realnym handlu po kwantyzacji.
"""
import random
from fractions import Fraction

from backend.core.types import Asset, Fill, Leg, Side
from backend.execution import PositionBook

_REL_TOL = 1e-9          # dryf względny, przy którym float przestaje być OK
_ABS_TOL = 1e-6          # dolar-owy próg absolutny dla wartości bliskich zeru


class _ExactLeg:
    """Ta sama logika co apply_to_leg, ale na Fraction (dokładna arytmetyka)."""

    def __init__(self) -> None:
        self.qty = Fraction(0)
        self.entry = Fraction(0)

    def apply(self, side: Side, price: Fraction, fill_qty: Fraction) -> Fraction:
        d = fill_qty if side == Side.BUY else -fill_qty
        realized = Fraction(0)
        if self.qty == 0 or (self.qty > 0) == (d > 0):
            new_qty = self.qty + d
            self.entry = price if self.qty == 0 else (
                (self.entry * self.qty + price * d) / new_qty)
            self.qty = new_qty
        else:
            closing = min(abs(d), abs(self.qty))
            realized = ((price - self.entry) * closing if self.qty > 0
                        else (self.entry - price) * closing)
            if abs(d) > abs(self.qty):
                self.entry = price
            self.qty = self.qty + d
        return realized


def _random_fills(n: int, seed: int) -> list[Fill]:
    rng = random.Random(seed)
    fills = []
    for i in range(n):
        asset = rng.choice([Asset.BTC, Asset.ETH, Asset.DOGE])
        leg = rng.choice([Leg.SPOT, Leg.PERP])
        side = rng.choice([Side.BUY, Side.SELL])
        # ceny/ilości Z SIATKI (jak po kwantyzacji): tick 0.01, step 0.001
        price = round(rng.uniform(0.05, 70_000.0), 2)
        qty = round(rng.uniform(0.001, 2.0), 3)
        fee = round(price * qty * 0.00075, 8)
        fills.append(Fill(f"c{i}", asset, leg, side, price, qty, fee, float(i)))
    return fills


def _close(a: float, b: Fraction) -> bool:
    bf = float(b)
    return abs(a - bf) <= max(_ABS_TOL, _REL_TOL * max(abs(a), abs(bf)))


def test_book_totals_match_exact_ledger_over_thousands_of_fills():
    fills = _random_fills(5_000, seed=42)
    book = PositionBook()
    exact_legs: dict = {}
    exact_realized = Fraction(0)
    exact_fees = Fraction(0)

    for f in fills:
        book.apply_fill(f, f.ts)
        leg = exact_legs.setdefault((f.asset, f.leg), _ExactLeg())
        exact_realized += leg.apply(f.side, Fraction(str(f.price)), Fraction(str(f.qty)))
        exact_fees += Fraction(str(f.fee))

    assert _close(book.realized_pnl, exact_realized), (
        f"dryf realized: float={book.realized_pnl!r} exact={float(exact_realized)!r}")
    assert _close(book.fees_paid, exact_fees)

    # pozycje per (aktywo, noga): wielkość i średnie wejście też bez dryfu
    for (asset, leg), ref in exact_legs.items():
        pos = book.position(asset)
        got_qty = pos.spot_qty if leg == Leg.SPOT else pos.perp_qty
        got_entry = pos.spot_entry if leg == Leg.SPOT else pos.perp_entry
        assert _close(got_qty, ref.qty), f"dryf qty {asset.value}/{leg.value}"
        if ref.qty != 0:
            assert _close(got_entry, ref.entry), f"dryf entry {asset.value}/{leg.value}"


def test_funding_accumulation_no_drift():
    book = PositionBook()
    book.apply_fill(Fill("s", Asset.BTC, Leg.SPOT, Side.BUY, 60_000.0, 1.0, 0.0, 1.0), 1.0)
    book.apply_fill(Fill("p", Asset.BTC, Leg.PERP, Side.SELL, 60_000.0, 1.0, 0.0, 1.0), 1.0)
    exact = Fraction(0)
    rng = random.Random(7)
    for _ in range(10_000):
        amt = round(rng.uniform(-0.5, 1.5), 8)            # typowe kwoty funding w $
        book.add_funding(Asset.BTC, amt)
        exact += Fraction(str(amt))
    assert _close(book.funding_collected, exact)
