"""Testy fundamentu: EventBus, typy, zegar."""
import asyncio

import pytest

from backend.core.bus import EventBus
from backend.core.clock import RealClock, SimClock
from backend.core.events import Event, EventType, Severity
from backend.core.types import (
    Asset,
    MarketTick,
    Position,
)


def make_tick(**kw) -> MarketTick:
    base = dict(
        asset=Asset.BTC,
        ts=1000.0,
        spot=60000.0,
        perp=60030.0,
        index=60000.0,
        funding_rate=0.0001,
        predicted_funding=0.0001,
        next_funding_ts=1000.0 + 3600,
        spot_bid=59999.0,
        spot_ask=60001.0,
        perp_bid=60029.0,
        perp_ask=60031.0,
        spot_depth_usd=500_000.0,
        perp_depth_usd=500_000.0,
        data_lag_ms=5.0,
    )
    base.update(kw)
    return MarketTick(**base)


def test_basis_bps_positive_premium():
    tick = make_tick(perp=60060.0, index=60000.0)
    # (60060-60000)/60000 = 0.001 = 10 bps
    assert tick.basis_bps == pytest.approx(10.0)


def test_basis_bps_zero_when_no_premium():
    assert make_tick(perp=60000.0, index=60000.0).basis_bps == pytest.approx(0.0)


def test_spread_and_funding_time():
    tick = make_tick()
    assert tick.spot_spread_bps > 0
    assert tick.perp_spread_bps > 0
    assert tick.seconds_to_funding == pytest.approx(3600.0)


def test_position_delta_neutral():
    pos = Position(id="p1", asset=Asset.BTC, spot_qty=1.0, spot_entry=60000.0,
                   perp_qty=-1.0, perp_entry=60030.0)
    assert pos.net_delta == pytest.approx(0.0)
    assert pos.is_open
    # spot +100, perp short zyskuje gdy perp spada; tu mark = entry → pnl 0 + funding 0
    assert pos.unrealized_pnl(60000.0, 60030.0) == pytest.approx(0.0)


def test_position_unrealized_pnl_directional_cancels():
    pos = Position(id="p2", asset=Asset.ETH, spot_qty=10.0, spot_entry=2000.0,
                   perp_qty=-10.0, perp_entry=2000.0)
    # cena rośnie o 50 na obu nogach: long spot +500, short perp -500 → netto 0
    assert pos.unrealized_pnl(2050.0, 2050.0) == pytest.approx(0.0)


def test_sim_clock():
    c = SimClock(100.0)
    assert c.now() == 100.0
    c.advance(5.0)
    assert c.now() == 105.0
    c.set(200.0)
    assert c.now() == 200.0


def test_real_clock_monotonic_ish():
    assert RealClock().now() > 0


def test_event_bus_sync_and_async_handlers():
    bus = EventBus()
    seen: list[str] = []

    def sync_handler(e: Event):
        seen.append(f"sync:{e.type.value}")

    async def async_handler(e: Event):
        await asyncio.sleep(0)
        seen.append(f"async:{e.type.value}")

    bus.subscribe(EventType.MARKET_TICK, sync_handler)
    bus.subscribe(EventType.MARKET_TICK, async_handler)

    ev = Event(type=EventType.MARKET_TICK, ts=1.0, source="test")
    asyncio.run(bus.publish(ev))

    assert "sync:MARKET_TICK" in seen
    assert "async:MARKET_TICK" in seen
    assert len(bus.history) == 1


def test_event_bus_subscribe_all_and_filtering():
    bus = EventBus()
    count = {"n": 0}
    bus.subscribe_all(lambda e: count.__setitem__("n", count["n"] + 1))

    asyncio.run(bus.publish(Event(EventType.FILL, 1.0, "t")))
    asyncio.run(bus.publish(Event(EventType.RISK_APPROVED, 2.0, "t")))

    assert count["n"] == 2
    assert len(bus.history_of(EventType.FILL)) == 1


def test_event_bus_isolates_handler_errors():
    bus = EventBus()
    ok = {"called": False}

    def bad(e):
        raise RuntimeError("boom")

    def good(e):
        ok["called"] = True

    bus.subscribe(EventType.FILL, bad)
    bus.subscribe(EventType.FILL, good)
    asyncio.run(bus.publish(Event(EventType.FILL, 1.0, "t", severity=Severity.INFO)))
    assert ok["called"] is True  # zły handler nie zablokował dobrego
