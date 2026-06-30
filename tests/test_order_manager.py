"""Chaos/recovery testy Order Managera — INVARIANT: po każdej próbie pozycja jest
albo delta-neutral, albo flat. Nigdy orphan leg."""
import asyncio

from backend.adapters.exchange import PaperBrokerAdapter
from backend.adapters.exchange.base import ExchangeAdapter, OrderResult
from backend.core.clock import SimClock
from backend.core.types import Asset, Fill, Leg, OrderRequest, OrderStatus, Side
from backend.execution import OrderManager, PairState, PositionBook


# -- pomocnicze brokery testowe --------------------------------------------- #
class FlakyBroker(ExchangeAdapter):
    """Odrzuca pierwsze `fail_first` prób każdej nogi, potem wypełnia (transient)."""
    name = "flaky"

    def __init__(self, fail_first: int = 1) -> None:
        self.fail_first = fail_first
        self._fails: dict = {}

    async def submit(self, req: OrderRequest) -> OrderResult:
        n = self._fails.get(req.leg, 0)
        if n < self.fail_first:
            self._fails[req.leg] = n + 1
            return OrderResult(OrderStatus.REJECTED, [], "transient")
        fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, req.qty, 0.0, req.ts)
        return OrderResult(OrderStatus.FILLED, [fill])


class PartialThenFullBroker(ExchangeAdapter):
    """Pierwsza próba nogi = partial (50%), kolejne = pełne wypełnienie reszty."""
    name = "partial"

    def __init__(self) -> None:
        self._seen: dict = {}

    async def submit(self, req: OrderRequest) -> OrderResult:
        if not self._seen.get(req.leg):
            self._seen[req.leg] = True
            half = req.qty * 0.5
            fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, half, 0.0, req.ts)
            return OrderResult(OrderStatus.PARTIALLY_FILLED, [fill])
        fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, req.qty, 0.0, req.ts)
        return OrderResult(OrderStatus.FILLED, [fill])


def _assert_invariant(book: PositionBook, asset: Asset) -> None:
    pos = book.position(asset)
    if pos is None or not pos.is_open:
        return  # flat = OK
    assert abs(pos.net_delta) < 0.02 * max(1e-9, abs(pos.spot_qty)), \
        f"ORPHAN LEG! net_delta={pos.net_delta}"


def _om(broker, **kw) -> OrderManager:
    return OrderManager(broker, PositionBook(), clock=SimClock(), **kw)


# -- scenariusze ------------------------------------------------------------ #
def test_happy_open_is_balanced():
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.OPEN
    assert om.book.is_open(Asset.BTC)
    _assert_invariant(om.book, Asset.BTC)


def test_one_leg_rejected_aborts_to_flat():
    om = _om(PaperBrokerAdapter(reject_leg=Leg.PERP), max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.ABORTED
    assert not om.book.is_open(Asset.BTC)        # skompensowane do flat
    _assert_invariant(om.book, Asset.BTC)


def test_spot_leg_rejected_aborts_to_flat():
    om = _om(PaperBrokerAdapter(reject_leg=Leg.SPOT), max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.ABORTED
    assert not om.book.is_open(Asset.BTC)
    _assert_invariant(om.book, Asset.BTC)


def test_transient_failure_retries_to_open():
    om = _om(FlakyBroker(fail_first=1), max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.OPEN          # retry doprowadził do pełnej pary
    _assert_invariant(om.book, Asset.BTC)


def test_partial_then_complete_opens():
    om = _om(PartialThenFullBroker(), max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.OPEN          # dosyłka reszty po partial
    _assert_invariant(om.book, Asset.BTC)


def test_close_pair_flattens():
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    asyncio.run(om.close_pair(Asset.BTC))
    assert not om.book.is_open(Asset.BTC)


def test_unique_coids_no_duplicates_on_retry():
    om = _om(PaperBrokerAdapter(reject_leg=Leg.PERP), max_attempts=3)
    asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    coids = list(om._orders.keys())
    assert len(coids) == len(set(coids))         # każda próba = unikalny coid


def test_reconcile_reports_clean_state():
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    rec = om.reconcile()
    assert "BTC" in rec["open_positions"]
    assert rec["imbalanced"] == []
    assert rec["pending_orders"] == []
