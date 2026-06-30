"""Testy cost telemetry/shadow: realny poślizg, fee, spread, lag vs model."""
import asyncio

from backend.adapters.exchange import PaperBrokerAdapter
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType
from backend.core.types import (
    Asset,
    Fill,
    Leg,
    MarketTick,
    OrderRequest,
    OrderType,
    Side,
)
from backend.execution import OrderManager, PositionBook
from backend.monitoring import CostTelemetry, Running


def _tick(perp=100.0, *, bid=99.95, ask=100.05, lag=12.0) -> MarketTick:
    return MarketTick(asset=Asset.BTC, ts=1.0, spot=perp, perp=perp, index=perp,
                      funding_rate=0.0, predicted_funding=0.0, next_funding_ts=2.0,
                      spot_bid=bid, spot_ask=ask, perp_bid=bid, perp_ask=ask,
                      spot_depth_usd=1e6, perp_depth_usd=1e6, data_lag_ms=lag)


def _req(coid, side, leg, price, qty=1.0) -> OrderRequest:
    return OrderRequest(coid, Asset.BTC, leg, side, OrderType.MARKET, price, qty, 1.0, "OPEN")


def _fill(coid, side, leg, price, qty=1.0, fee=0.0) -> Fill:
    return Fill(coid, Asset.BTC, leg, side, price, qty, fee, 1.0)


def _feed(tel: CostTelemetry, *events: Event) -> None:
    async def go():
        for e in events:
            if e.type == EventType.ORDER_REQUEST:
                await tel._on_req(e)
            elif e.type in (EventType.FILL, EventType.PARTIAL_FILL):
                await tel._on_fill(e)
            else:
                await tel._on_tick(e)
    asyncio.run(go())


# -- Running ---------------------------------------------------------------- #
def test_running_mean_min_max():
    r = Running()
    for x in (10.0, 20.0, 30.0):
        r.add(x)
    s = r.snapshot()
    assert s == {"n": 3, "mean": 20.0, "min": 10.0, "max": 30.0}


def test_running_empty_is_zeroed():
    assert Running().snapshot() == {"n": 0, "mean": 0.0, "min": 0.0, "max": 0.0}


# -- poślizg egzekucji ------------------------------------------------------ #
def test_buy_slippage_positive_when_fill_above_ref():
    tel = CostTelemetry()
    _feed(tel,
          Event(EventType.ORDER_REQUEST, 1.0, "om", payload=_req("c1", Side.BUY, Leg.SPOT, 100.0)),
          Event(EventType.FILL, 1.0, "om", payload=_fill("c1", Side.BUY, Leg.SPOT, 100.1)))
    # fill 100.1 vs ref 100 = +10 bps kosztu
    assert abs(tel.slippage_bps[Leg.SPOT].mean - 10.0) < 1e-6


def test_sell_slippage_positive_when_fill_below_ref():
    tel = CostTelemetry()
    _feed(tel,
          Event(EventType.ORDER_REQUEST, 1.0, "om", payload=_req("c2", Side.SELL, Leg.PERP, 100.0)),
          Event(EventType.FILL, 1.0, "om", payload=_fill("c2", Side.SELL, Leg.PERP, 99.9)))
    assert abs(tel.slippage_bps[Leg.PERP].mean - 10.0) < 1e-6


def test_fee_bps_measured():
    tel = CostTelemetry()
    # fee 0.75 na nominale 100 = 75 bps
    _feed(tel,
          Event(EventType.ORDER_REQUEST, 1.0, "om", payload=_req("c3", Side.BUY, Leg.SPOT, 100.0)),
          Event(EventType.FILL, 1.0, "om", payload=_fill("c3", Side.BUY, Leg.SPOT, 100.0, qty=1.0, fee=0.75)))
    assert abs(tel.fee_bps[Leg.SPOT].mean - 75.0) < 1e-6


def test_fill_without_ref_skips_slippage_but_counts_fee():
    tel = CostTelemetry()
    _feed(tel, Event(EventType.FILL, 1.0, "om",
                     payload=_fill("orphan", Side.BUY, Leg.SPOT, 100.0, fee=0.5)))
    assert tel.slippage_bps[Leg.SPOT].n == 0       # brak referencji → bez poślizgu
    assert tel.fee_bps[Leg.SPOT].n == 1            # fee i tak liczone


# -- rynek: spread + lag ---------------------------------------------------- #
def test_market_spread_and_lag_tracked():
    tel = CostTelemetry()
    _feed(tel, Event(EventType.MARKET_TICK, 1.0, "md", payload=_tick(bid=99.9, ask=100.1, lag=20.0)))
    assert tel.spread_bps_spot.n == 1
    assert tel.spread_bps_spot.mean > 0
    assert abs(tel.data_lag_ms.mean - 20.0) < 1e-6


# -- ograniczenie pamięci referencji ---------------------------------------- #
def test_ref_memory_is_bounded():
    tel = CostTelemetry(ref_maxlen=5)
    for i in range(20):
        asyncio.run(tel._on_req(Event(EventType.ORDER_REQUEST, 1.0, "om",
                                      payload=_req(f"c{i}", Side.BUY, Leg.SPOT, 100.0))))
    assert len(tel._ref) <= 5


# -- snapshot --------------------------------------------------------------- #
def test_snapshot_structure():
    snap = CostTelemetry().snapshot()
    assert set(snap) == {"slippage_bps", "fee_bps", "spread_bps", "data_lag_ms", "pending_refs"}
    assert set(snap["slippage_bps"]) == {"SPOT", "PERP"}
    assert set(snap["spread_bps"]) == {"spot", "perp"}


# -- integracja z OrderManagerem na szynie ---------------------------------- #
def test_integration_measures_real_paper_costs():
    bus = EventBus()
    tel = CostTelemetry()
    tel.attach(bus)
    om = OrderManager(PaperBrokerAdapter(slippage_bps=10.0, fee_bps_spot=7.5, fee_bps_perp=1.8),
                      PositionBook(), clock=SimClock(), bus=bus)
    asyncio.run(om.open_pair(Asset.BTC, 1.0, 1.0, 100.0, 100.0))

    # paper stosuje 10 bps poślizgu na każdą nogę (BUY spot drożej, SELL perp taniej)
    assert abs(tel.slippage_bps[Leg.SPOT].mean - 10.0) < 1e-6
    assert abs(tel.slippage_bps[Leg.PERP].mean - 10.0) < 1e-6
    # realne fee bps ≈ skonfigurowane stawki
    assert abs(tel.fee_bps[Leg.SPOT].mean - 7.5) < 1e-6
    assert abs(tel.fee_bps[Leg.PERP].mean - 1.8) < 1e-6
