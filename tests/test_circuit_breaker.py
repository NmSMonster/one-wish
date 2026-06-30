"""Testy: circuit breakers reżimu + integracja z RiskManagerem."""
import asyncio

from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType
from backend.core.types import Asset, MarketTick, TradeIntent
from backend.risk import CircuitBreaker, RiskConfig, RiskManager


def _tick(*, premium_bps=0.0, depth=1e6, oi=0.0, ts=1.0) -> MarketTick:
    index = 100.0
    mark = index * (1 + premium_bps / 1e4)
    return MarketTick(asset=Asset.BTC, ts=ts, spot=index, perp=index, index=index,
                      funding_rate=0.0, predicted_funding=0.0, next_funding_ts=ts + 1,
                      spot_bid=index - 1, spot_ask=index + 1, perp_bid=index - 1, perp_ask=index + 1,
                      spot_depth_usd=depth, perp_depth_usd=depth, mark_price=mark,
                      open_interest_usd=oi)


def _publish(bus, tick):
    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, tick.ts, "s", payload=tick)))


def test_premium_anomaly_trips_breaker():
    bus = EventBus()
    cb = CircuitBreaker(max_abs_premium_bps=40.0)
    cb.attach(bus)
    _publish(bus, _tick(premium_bps=80.0))      # premia 80bps > 40
    assert cb.is_open(Asset.BTC)


def test_thin_depth_trips_breaker():
    bus = EventBus()
    cb = CircuitBreaker(min_depth_usd=20_000.0)
    cb.attach(bus)
    _publish(bus, _tick(depth=5_000.0))
    assert cb.is_open(Asset.BTC)


def test_oi_spike_trips_breaker():
    bus = EventBus()
    cb = CircuitBreaker(oi_jump_frac=0.30)
    cb.attach(bus)
    _publish(bus, _tick(oi=1_000_000.0, ts=1.0))   # baseline OI
    _publish(bus, _tick(oi=1_500_000.0, ts=2.0))   # +50% skok > 30%
    assert cb.is_open(Asset.BTC)


def test_liquidation_cascade_trips_breaker():
    bus = EventBus()
    cb = CircuitBreaker(max_liq_notional_usd=1_000_000.0, liq_window_s=60.0)
    cb.attach(bus)
    cb.note_liquidation(Asset.BTC, 2_000_000.0, ts=5.0)   # kaskada w oknie
    _publish(bus, _tick(ts=10.0))
    assert cb.is_open(Asset.BTC)


def test_breaker_resets_when_normal():
    bus = EventBus()
    cb = CircuitBreaker(max_abs_premium_bps=40.0)
    cb.attach(bus)
    _publish(bus, _tick(premium_bps=80.0))
    assert cb.is_open(Asset.BTC)
    _publish(bus, _tick(premium_bps=2.0))        # wraca do normy
    assert not cb.is_open(Asset.BTC)


def test_normal_tick_does_not_trip():
    bus = EventBus()
    cb = CircuitBreaker()
    cb.attach(bus)
    _publish(bus, _tick(premium_bps=3.0, depth=1e6, oi=0.0))
    assert not cb.is_open(Asset.BTC)


def test_risk_manager_rejects_open_when_circuit_open():
    cb = CircuitBreaker(max_abs_premium_bps=40.0)
    bus = EventBus()
    cb.attach(bus)
    rm = RiskManager(RiskConfig(max_spread_bps=1000.0, min_depth_usd=0.0),
                     clock=SimClock(1.0), circuit=cb)

    _publish(bus, _tick(premium_bps=80.0))       # bezpiecznik otwarty
    intent = TradeIntent(Asset.BTC, 1.0, "OPEN", 100.0, 5.0)
    d = rm.approve(intent, _tick(premium_bps=80.0))
    assert not d.approved and "circuit breaker" in d.reason
