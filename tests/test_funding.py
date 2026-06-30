"""Testy: FundingAccrual — short perp inkasuje funding przy rozliczeniu."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import Asset, Fill, Leg, MarketTick, Side
from backend.execution import PositionBook
from backend.execution.funding import FundingAccrual


def btc_tick(ts, next_funding_ts, funding=0.0002, perp=60_000.0) -> MarketTick:
    return MarketTick(asset=Asset.BTC, ts=ts, spot=perp, perp=perp, index=perp,
                      funding_rate=funding, predicted_funding=funding,
                      next_funding_ts=next_funding_ts,
                      spot_bid=perp - 1, spot_ask=perp + 1, perp_bid=perp - 1, perp_ask=perp + 1,
                      spot_depth_usd=1e6, perp_depth_usd=1e6, data_lag_ms=1.0)


def _open_delta_neutral(book, qty=0.01, px=60_000.0):
    book.apply_fill(Fill("a", Asset.BTC, Leg.SPOT, Side.BUY, px, qty, 0.0, 1.0), 1.0)
    book.apply_fill(Fill("b", Asset.BTC, Leg.PERP, Side.SELL, px, qty, 0.0, 1.0), 1.0)


def test_short_perp_receives_positive_funding():
    book = PositionBook()
    _open_delta_neutral(book)
    bus = EventBus()
    FundingAccrual(book).attach(bus)
    accr: list = []
    bus.subscribe(EventType.FUNDING_ACCRUED, lambda e: accr.append(e.payload))

    async def run():
        await bus.publish(Event(EventType.MARKET_TICK, 10.0, "s", payload=btc_tick(10.0, 100.0)))   # baseline
        await bus.publish(Event(EventType.MARKET_TICK, 110.0, "s", payload=btc_tick(110.0, 200.0)))  # crossing

    asyncio.run(run())
    assert len(accr) == 1
    # funding = -(-0.01) * 0.0002 * 60000 = 0.12$
    assert abs(accr[0]["amount"] - 0.12) < 1e-9
    assert abs(book.funding_collected - 0.12) < 1e-9


def test_no_funding_without_open_position():
    book = PositionBook()
    bus = EventBus()
    FundingAccrual(book).attach(bus)
    accr: list = []
    bus.subscribe(EventType.FUNDING_ACCRUED, lambda e: accr.append(e.payload))

    async def run():
        await bus.publish(Event(EventType.MARKET_TICK, 10.0, "s", payload=btc_tick(10.0, 100.0)))
        await bus.publish(Event(EventType.MARKET_TICK, 110.0, "s", payload=btc_tick(110.0, 200.0)))

    asyncio.run(run())
    assert accr == []


def test_no_funding_without_settlement_crossing():
    book = PositionBook()
    _open_delta_neutral(book)
    bus = EventBus()
    FundingAccrual(book).attach(bus)

    async def run():
        await bus.publish(Event(EventType.MARKET_TICK, 10.0, "s", payload=btc_tick(10.0, 100.0)))
        await bus.publish(Event(EventType.MARKET_TICK, 50.0, "s", payload=btc_tick(50.0, 100.0)))  # bez zmiany

    asyncio.run(run())
    assert book.funding_collected == 0.0
