"""Testy M11: Monitor, OneWishApp i ścieżka awaryjna EMERGENCY_STOP."""
import asyncio

from backend.adapters.exchange import PaperBrokerAdapter
from backend.app.pipeline import Pipeline
from backend.app.runner import OneWishApp
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType
from backend.core.types import Asset, PnLSnapshot
from backend.execution import PositionBook
from backend.monitoring import Monitor
from backend.risk import RiskConfig
from tests.test_repricing import tick_with


def _generous() -> RiskConfig:
    return RiskConfig(max_trade_notional_usd=1000.0, max_asset_exposure_usd=1_000_000.0,
                      max_total_exposure_usd=1e12, max_open_positions=10,
                      max_trades_per_day=10**9, max_spread_bps=100.0, min_depth_usd=0.0,
                      max_daily_loss_usd=50.0)


def test_monitor_emergency_on_daily_loss():
    bus = EventBus()
    stops: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))

    mon = Monitor(max_daily_loss_usd=50.0)
    mon.attach(bus)

    async def run():
        await bus.publish(Event(EventType.PNL_UPDATE, 1.0, "e",
                                payload=PnLSnapshot(1.0, -60.0, 0.0, 0.0, 0.0)))
        # drugi raz nie powinien dublować stopu
        await bus.publish(Event(EventType.PNL_UPDATE, 2.0, "e",
                                payload=PnLSnapshot(2.0, -70.0, 0.0, 0.0, 0.0)))

    asyncio.run(run())
    assert len(stops) == 1
    assert "limit straty" in stops[0]["reason"]


def test_emergency_stop_flattens_and_kills():
    bus = EventBus()
    pipe = Pipeline(bus, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1),
                    clock=SimClock())

    async def run():
        # silna dyslokacja → otwarcie pozycji przez cały łańcuch
        await bus.publish(Event(EventType.MARKET_TICK, 1.0, "s", payload=tick_with(60.0)))
        assert pipe.book.is_open(Asset.BTC)
        # awaryjny stop → kill ryzyka + domknięcie pozycji
        await bus.publish(Event(EventType.EMERGENCY_STOP, 2.0, "mon", payload={"reason": "test"}))
        assert pipe.risk.is_killed
        assert not pipe.book.is_open(Asset.BTC)

    asyncio.run(run())


def test_stale_feed_midsequence_triggers_emergency_flatten():
    """Chaos #7: luka w feedzie PO otwarciu pozycji → STALE_FEED → EMERGENCY_STOP →
    awaryjne domknięcie. Inwariant: po chaosie flat + kill."""
    from dataclasses import replace

    from backend.adapters.market.base import MarketDataAdapter, MarketSource

    class _ListSource(MarketSource):
        def __init__(self, ticks):
            self._ticks = ticks

        async def ticks(self):
            for t in self._ticks:
                yield t

    bus = EventBus()
    clock = SimClock()
    pipe = Pipeline(bus, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1), clock=clock)
    Monitor(bus, clock=clock).attach(bus)

    open_tick = tick_with(60.0)                          # ts=1000 → otwiera (dyslokacja)
    gap_tick = replace(tick_with(0.0), ts=1100.0)        # luka 100s > stale_after_s
    adapter = MarketDataAdapter(_ListSource([open_tick, gap_tick]), bus, clock, stale_after_s=5.0)

    stops: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))

    asyncio.run(adapter.run())

    assert stops, "STALE_FEED powinien eskalować do EMERGENCY_STOP"
    assert pipe.risk.is_killed
    assert not pipe.book.is_open(Asset.BTC)              # awaryjnie domknięte → flat


def test_app_synthetic_runs_and_reports():
    app = OneWishApp(mode="synthetic", steps=200, seed=3, gui=False,
                     risk_config=_generous(), notional_usd=200.0)
    report = asyncio.run(app.run())
    assert report.counts.get("MARKET_TICK", 0) > 0
    assert isinstance(report.net, float)
    assert "RAPORT DZIENNY" in report.summary()
    assert report.counts.get("EDGE_DETECTED", 0) > 0
