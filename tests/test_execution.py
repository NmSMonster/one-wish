"""Testy M7: PositionBook, PaperBroker i pełny pipeline end-to-end."""
import asyncio

from backend.adapters.exchange import PaperBrokerAdapter
from backend.adapters.market import MarketDataAdapter, SyntheticSource
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import EventType
from backend.core.types import Asset, Fill, Leg, OrderRequest, OrderStatus, OrderType, Side
from backend.execution import ExecutionEngine, PositionBook
from backend.execution.book import apply_to_leg
from backend.model.costs import CostModel
from backend.model.fair_value import FairValueModel
from backend.risk import RiskConfig, RiskManager
from backend.signal import RepricingDetector
from backend.strategy import StrategyPolicy


# -- matematyka księgi ------------------------------------------------------ #
def test_long_roundtrip_realizes_pnl():
    qty, entry, r1 = apply_to_leg(0.0, 0.0, Side.BUY, 100.0, 1.0)
    assert qty == 1.0 and entry == 100.0 and r1 == 0.0
    qty, entry, r2 = apply_to_leg(qty, entry, Side.SELL, 110.0, 1.0)
    assert qty == 0.0 and r2 == 10.0          # long zarabia gdy cena rośnie


def test_short_roundtrip_realizes_pnl():
    qty, entry, _ = apply_to_leg(0.0, 0.0, Side.SELL, 100.0, 1.0)
    assert qty == -1.0 and entry == 100.0
    qty, entry, r = apply_to_leg(qty, entry, Side.BUY, 90.0, 1.0)
    assert qty == 0.0 and r == 10.0           # short zarabia gdy cena spada


def test_delta_neutral_roundtrip_nets_zero_before_fees():
    book = PositionBook()
    # otwarcie: long spot + short perp po 100
    book.apply_fill(Fill("a", Asset.BTC, Leg.SPOT, Side.BUY, 100.0, 1.0, 0.0, 1.0), 1.0)
    book.apply_fill(Fill("b", Asset.BTC, Leg.PERP, Side.SELL, 100.0, 1.0, 0.0, 1.0), 1.0)
    # cena rośnie do 110 na obu nogach, zamknięcie
    book.apply_fill(Fill("c", Asset.BTC, Leg.SPOT, Side.SELL, 110.0, 1.0, 0.0, 2.0), 2.0)
    book.apply_fill(Fill("d", Asset.BTC, Leg.PERP, Side.BUY, 110.0, 1.0, 0.0, 2.0), 2.0)
    assert abs(book.realized_pnl) < 1e-9      # delta-neutral: +10 spot − 10 perp = 0
    assert not book.is_open(Asset.BTC)


# -- paper broker ----------------------------------------------------------- #
def test_paper_broker_fills_with_slippage_and_fee():
    broker = PaperBrokerAdapter(slippage_bps=10.0, fee_bps_spot=7.5)
    req = OrderRequest("x", Asset.BTC, Leg.SPOT, Side.BUY, OrderType.MARKET, 100.0, 1.0, 1.0)
    res = asyncio.run(broker.submit(req))
    assert res.status == OrderStatus.FILLED
    fill = res.fills[0]
    assert fill.price > 100.0                 # kup → drożej (poślizg)
    assert fill.fee > 0.0


def test_paper_broker_rejects_when_configured():
    broker = PaperBrokerAdapter(reject_prob=1.0)
    req = OrderRequest("x", Asset.BTC, Leg.PERP, Side.SELL, OrderType.MARKET, 100.0, 1.0, 1.0)
    res = asyncio.run(broker.submit(req))
    assert res.status == OrderStatus.REJECTED and not res.fills


def test_paper_broker_partial_fill():
    broker = PaperBrokerAdapter(partial_prob=1.0)
    req = OrderRequest("x", Asset.BTC, Leg.SPOT, Side.BUY, OrderType.MARKET, 100.0, 2.0, 1.0)
    res = asyncio.run(broker.submit(req))
    assert res.status == OrderStatus.PARTIALLY_FILLED
    assert res.fills[0].qty == 1.0


# -- pełny pipeline end-to-end --------------------------------------------- #
def _generous_risk():
    return RiskConfig(max_trade_notional_usd=1000.0, max_asset_exposure_usd=1_000_000.0,
                      max_total_exposure_usd=1e12, max_open_positions=10,
                      max_trades_per_day=10**9, max_spread_bps=100.0, min_depth_usd=0.0)


def test_full_pipeline_opens_closes_and_books_pnl():
    bus = EventBus()
    book = PositionBook()
    broker = PaperBrokerAdapter(slippage_bps=1.0, seed=1)

    risk = RiskManager(_generous_risk(), clock=SimClock())
    engine = ExecutionEngine(broker, book, clock=SimClock())
    detector = RepricingDetector(FairValueModel(), CostModel())
    policy = StrategyPolicy(notional_usd=200.0)

    # kolejność: cache ticka (risk, engine) PRZED detektorem emitującym edge
    risk.attach(bus)
    engine.attach(bus)
    detector.attach(bus)
    policy.attach(bus)

    counts = {EventType.POSITION_OPENED: 0, EventType.POSITION_CLOSED: 0, EventType.FILL: 0}
    for et in counts:
        bus.subscribe(et, lambda e, et=et: counts.__setitem__(et, counts[et] + 1))

    src = SyntheticSource(steps=250, seed=3)
    adapter = MarketDataAdapter(src, bus, SimClock())
    asyncio.run(adapter.run())

    assert counts[EventType.POSITION_OPENED] > 0
    # brak rejectów/partiali → 2 fille na otwarcie i 2 na zamknięcie
    assert counts[EventType.FILL] == 2 * counts[EventType.POSITION_OPENED] + 2 * counts[EventType.POSITION_CLOSED]
    assert book.fees_paid > 0.0
    assert isinstance(book.realized_pnl, float)

    # każda wciąż otwarta pozycja jest delta-neutral (mała ekspozycja kierunkowa)
    for asset, pos in book.positions.items():
        if pos.is_open:
            assert abs(pos.net_delta * pos.spot_entry) < 5.0

    rec = engine.reconcile()
    assert "open_positions" in rec and "open_orders" in rec
