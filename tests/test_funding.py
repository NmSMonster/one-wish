"""Testy: FundingAccrual — short perp inkasuje funding przy rozliczeniu."""
import asyncio

import pytest

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


def test_snapshot_net_counts_funding_once_not_twice():
    """Regresja: dla OTWARTEJ pozycji funding trafiał i do `unrealized` (bo
    unrealized_pnl go dodawał) i do `funding_collected` → PnLSnapshot.net podwajał
    funding. Po fixie snapshot liczy czysty mark-to-market, więc net liczy funding RAZ."""
    book = PositionBook()
    _open_delta_neutral(book)                       # short 0.01 @ 60k, delta-neutral
    book.add_funding(Asset.BTC, 0.12)               # zainkasowany funding
    # marki = entry → mark-to-market = 0; jedyny wynik to funding
    snap = book.snapshot({Asset.BTC: (60_000.0, 60_000.0)}, 5.0)
    assert snap.unrealized == 0.0                   # czysty MtM bez funding
    assert snap.funding_collected == 0.12
    assert snap.net == 0.12                          # RAZ, nie 0.24

    # sanity: karta pozycji nadal pokazuje funding w unrealized_pnl (per-pozycja),
    # a price_pnl jest czysty
    pos = book.position(Asset.BTC)
    assert pos.price_pnl(60_000.0, 60_000.0) == 0.0
    assert pos.unrealized_pnl(60_000.0, 60_000.0) == pytest.approx(0.12)
