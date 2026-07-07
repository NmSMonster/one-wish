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


class StuckPerpBroker(ExchangeAdapter):
    """Spot wypełnia w pełni; perp przy WEJŚCIU domyka tylko 50% reszty (nigdy nie
    domknie pary) — najgroźniejszy orphan: spot OK, perp wisi. Unwind (CLOSE) działa
    normalnie (utknięcie było na wejściu; wyjście z resztki musi być możliwe)."""
    name = "stuck_perp"

    async def submit(self, req: OrderRequest) -> OrderResult:
        if req.leg == Leg.PERP and req.intent_action == "OPEN":
            half = req.qty * 0.5
            fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, half, 0.0, req.ts)
            return OrderResult(OrderStatus.PARTIALLY_FILLED, [fill])
        fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, req.qty, 0.0, req.ts)
        return OrderResult(OrderStatus.FILLED, [fill])


class SpotOkPerpDeadBroker(ExchangeAdapter):
    """Spot: partial potem pełne. Perp: zawsze odrzucone (martwa noga) — mieszany
    chaos (niepełne spot + martwy perp) musi i tak skończyć się flat."""
    name = "spot_ok_perp_dead"

    def __init__(self) -> None:
        self._spot_seen = False

    async def submit(self, req: OrderRequest) -> OrderResult:
        if req.leg == Leg.PERP:
            return OrderResult(OrderStatus.REJECTED, [], "perp dead")
        if not self._spot_seen:
            self._spot_seen = True
            half = req.qty * 0.5
            fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, half, 0.0, req.ts)
            return OrderResult(OrderStatus.PARTIALLY_FILLED, [fill])
        fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, req.qty, 0.0, req.ts)
        return OrderResult(OrderStatus.FILLED, [fill])


class LostAckFilledBroker(ExchangeAdapter):
    """Każdy submit gubi ack (uncertain), ALE zlecenie wchodzi i wypełnia się w pełni.
    query_order to potwierdza. OM nie może złożyć drugiego zlecenia (podwójny fill)."""
    name = "lost_ack_filled"

    def __init__(self) -> None:
        self.submits = 0
        self._last: dict = {}

    async def submit(self, req: OrderRequest) -> OrderResult:
        self.submits += 1
        self._last[req.leg] = req
        return OrderResult(OrderStatus.REJECTED, [], "timeout", uncertain=True)

    async def query_order(self, asset, leg, coid):
        r = self._last[leg]
        fill = Fill(coid, asset, leg, r.side, r.price, r.qty, 0.0, r.ts)
        return OrderResult(OrderStatus.FILLED, [fill])


class LostAckAbsentBroker(ExchangeAdapter):
    """Pierwsza próba nogi: ack zgubiony, ale zlecenie NIE dotarło (query → None).
    Kolejna próba: normalny pełny fill."""
    name = "lost_ack_absent"

    def __init__(self) -> None:
        self._seen: dict = {}
        self.submits = 0

    async def submit(self, req: OrderRequest) -> OrderResult:
        self.submits += 1
        if not self._seen.get(req.leg):
            self._seen[req.leg] = True
            return OrderResult(OrderStatus.REJECTED, [], "timeout", uncertain=True)
        fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, req.qty, 0.0, req.ts)
        return OrderResult(OrderStatus.FILLED, [fill])

    async def query_order(self, asset, leg, coid):
        return None                       # zlecenie nie zostało złożone


class LostAckQueryFailsBroker(ExchangeAdapter):
    """Spot fillsuje normalnie; perp gubi ack, a query_order PADA (stan nieustalony).
    OM nie może ponowić perpa (ryzyko dubla) → para kompensowana do flat."""
    name = "lost_ack_query_fails"

    def __init__(self) -> None:
        self.perp_submits = 0

    async def submit(self, req: OrderRequest) -> OrderResult:
        if req.leg == Leg.PERP:
            self.perp_submits += 1
            return OrderResult(OrderStatus.REJECTED, [], "timeout", uncertain=True)
        fill = Fill(req.client_order_id, req.asset, req.leg, req.side, req.price, req.qty, 0.0, req.ts)
        return OrderResult(OrderStatus.FILLED, [fill])

    async def query_order(self, asset, leg, coid):
        raise OSError("query down")       # stanu nie da się ustalić


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


def test_close_pair_uses_market_price_not_entry():
    """Regresja P0: zamknięcie po cenie WEJŚCIA fałszowało PnL — paper broker
    fill'uje po req.price, więc cały ruch ceny od entry znikał z realized.
    Scenariusz: wejście @100/100, rynek: spot 130 / perp 120 (basis się zbiegł
    z premii do dyskonta). Zamknięcie po rynku → realized = +30 (spot) − 20 (perp)
    = +10. Ze starym bugiem (entry-close) realized byłby 0."""
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    asyncio.run(om.open_pair(Asset.BTC, 1.0, 1.0, 100.0, 100.0))
    asyncio.run(om.close_pair(Asset.BTC, spot_px=130.0, perp_px=120.0))
    assert not om.book.is_open(Asset.BTC)
    assert abs(om.book.realized_pnl - 10.0) < 1e-9


def test_avg_price_is_qty_weighted_across_partials():
    """Regresja: avg_price brał cenę OSTATNIEGO filla — po dosyłce reszty po
    partial-fill średnia była przekłamana. Ma być średnia ważona ilością."""
    from backend.execution.order_manager import ManagedOrder, OrderManager
    from backend.core.types import Fill, Leg, Side

    mo = ManagedOrder(asset=Asset.BTC, leg=Leg.SPOT, side=Side.BUY,
                      target_qty=1.0, price=100.0)
    OrderManager._apply_fill_to_order(
        mo, Fill("c", Asset.BTC, Leg.SPOT, Side.BUY, 100.0, 0.75, 0.0, 1.0))
    OrderManager._apply_fill_to_order(
        mo, Fill("c", Asset.BTC, Leg.SPOT, Side.BUY, 120.0, 0.25, 0.0, 1.0))
    assert abs(mo.filled_qty - 1.0) < 1e-12
    assert abs(mo.avg_price - 105.0) < 1e-9        # 0.75×100 + 0.25×120


def test_close_pair_without_price_falls_back_to_entry():
    """Bez podanych cen (np. testy jednostkowe) — fallback do entry, jak dotąd."""
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    asyncio.run(om.open_pair(Asset.BTC, 1.0, 1.0, 100.0, 100.0))
    asyncio.run(om.close_pair(Asset.BTC))
    assert not om.book.is_open(Asset.BTC)
    assert abs(om.book.realized_pnl) < 1e-9


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


# -- chaos #7: martwe nogi, restart między nogami, recovery ----------------- #
def test_perp_never_completes_aborts_to_flat():
    # spot OK, perp wisi w partialu po wyczerpaniu prób → kompensacja do flat
    om = _om(StuckPerpBroker(), max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.ABORTED
    assert not om.book.is_open(Asset.BTC)
    _assert_invariant(om.book, Asset.BTC)


def test_mixed_spot_partial_perp_dead_aborts_to_flat():
    # spot domyka się po partialu, perp całkiem martwy → i tak kończymy flat
    om = _om(SpotOkPerpDeadBroker(), max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.ABORTED
    assert not om.book.is_open(Asset.BTC)
    _assert_invariant(om.book, Asset.BTC)


def test_restart_between_legs_detected_and_recovered():
    # symulacja crashu PO nodze spot, PRZED perp: zostaje orphan spot
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    asyncio.run(om._submit(Asset.BTC, Leg.SPOT, Side.BUY, 0.01, 100.0, "OPEN"))
    assert om.book.is_open(Asset.BTC)

    rec = om.reconcile()
    assert "BTC" in rec["imbalanced"]            # reconcile wykrywa orphan po restarcie

    asyncio.run(om._flatten(Asset.BTC))          # recovery: kompensacja do flat
    assert not om.book.is_open(Asset.BTC)
    _assert_invariant(om.book, Asset.BTC)
    assert om.reconcile()["imbalanced"] == []    # czysto po recovery


def test_flatten_on_flat_is_noop():
    om = _om(PaperBrokerAdapter(slippage_bps=0.0))
    asyncio.run(om._flatten(Asset.BTC))          # nic otwartego → brak wyjątku, brak wejść
    assert not om.book.is_open(Asset.BTC)
    assert om.reconcile()["open_positions"] == []


# -- lost-ack: reconcile-before-retry (zero podwójnego filla) ---------------- #
def test_lost_ack_filled_reconciles_without_double_submit():
    broker = LostAckFilledBroker()
    om = _om(broker, max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.OPEN
    _assert_invariant(om.book, Asset.BTC)
    # KLUCZOWE: po jednym submitcie na nogę (ack zgubiony) reconcile potwierdził fill;
    # OM NIE złożył drugiego zlecenia → 2 submity (spot+perp), nie 4.
    assert broker.submits == 2
    pos = om.book.position(Asset.BTC)
    assert abs(pos.spot_qty - 0.01) < 1e-9       # dokładnie raz, bez dubla
    assert abs(pos.perp_qty + 0.01) < 1e-9


def test_lost_ack_absent_order_safely_retries():
    broker = LostAckAbsentBroker()
    om = _om(broker, max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.OPEN          # niezłożone → ponowiono i wypełniono
    _assert_invariant(om.book, Asset.BTC)
    # każda noga: 1x uncertain (absent) + 1x fill = 2 submity → razem 4
    assert broker.submits == 4


def test_lost_ack_query_failure_aborts_to_flat_no_retry():
    broker = LostAckQueryFailsBroker()
    om = _om(broker, max_attempts=3)
    pair = asyncio.run(om.open_pair(Asset.BTC, 0.01, 0.01, 100.0, 100.0))
    assert pair.state == PairState.ABORTED
    assert not om.book.is_open(Asset.BTC)        # skompensowane do flat
    _assert_invariant(om.book, Asset.BTC)
    # perp próbowany dokładnie RAZ (po nieustalonym stanie NIE ponawiamy)
    assert broker.perp_submits == 1
