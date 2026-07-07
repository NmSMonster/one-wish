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


def test_monitor_heartbeat_emergency_on_silent_feed():
    """Regresja P0: STALE_FEED z adaptera powstaje dopiero przy NASTĘPNYM ticku.
    Gdy feed MILKNIE całkiem, następny tick nigdy nie przychodzi — tylko heartbeat
    Monitora może wtedy wyemitować EMERGENCY_STOP (flatten + kill)."""
    from tests.test_funding import btc_tick

    async def scenario():
        bus = EventBus()
        stops: list = []
        bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))
        mon = Monitor(stale_after_s=0.05)          # RealClock — realny upływ czasu
        mon.attach(bus)
        mon.start_heartbeat(0.02)
        await bus.publish(Event(EventType.MARKET_TICK, 1.0, "s",
                                payload=btc_tick(1.0, 100.0)))
        await asyncio.sleep(0.3)                   # feed milknie — żadnych ticków
        mon.stop_heartbeat()
        return stops

    stops = asyncio.run(scenario())
    assert stops, "heartbeat powinien wykryć milczący feed i dać EMERGENCY_STOP"
    assert "milczy" in stops[0]["reason"]


def test_monitor_heartbeat_silent_while_feed_alive():
    """Heartbeat nie strzela, dopóki ticki przychodzą (brak fałszywych alarmów)."""
    from tests.test_funding import btc_tick

    async def scenario():
        bus = EventBus()
        stops: list = []
        bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))
        mon = Monitor(stale_after_s=0.2)
        mon.attach(bus)
        mon.start_heartbeat(0.02)
        for i in range(5):                         # żywy feed co 50ms < stale 200ms
            await bus.publish(Event(EventType.MARKET_TICK, float(i), "s",
                                    payload=btc_tick(float(i), 100.0)))
            await asyncio.sleep(0.05)
        mon.stop_heartbeat()
        return stops

    assert asyncio.run(scenario()) == []


def test_report_includes_unrealized_mark_to_market():
    """Regresja P1: net raportu musi zawierać mark-to-market OTWARTYCH pozycji —
    carry celowo trzyma, więc bez tego net udaje, że trzymane pary nie mają wyniku."""
    from backend.app.report import build_report
    from backend.core.types import Fill, Leg, Side
    from backend.storage import Database

    db = Database()
    book = PositionBook()
    book.apply_fill(Fill("s", Asset.BTC, Leg.SPOT, Side.BUY, 100.0, 1.0, 0.0, 1.0), 1.0)
    book.apply_fill(Fill("p", Asset.BTC, Leg.PERP, Side.SELL, 100.0, 1.0, 0.0, 1.0), 1.0)
    # marki: spot 130 / perp 120 → MtM = +30 − 20 = +10
    rep = build_report(db, book, marks={Asset.BTC: (130.0, 120.0)})
    assert abs(rep.unrealized - 10.0) < 1e-9
    assert abs(rep.net - 10.0) < 1e-9              # realized=0, fees=0, funding=0
    assert "niezrealiz" in rep.summary()
    # bez marks — zachowanie jak dotąd (unrealized 0)
    rep0 = build_report(db, book)
    assert rep0.unrealized == 0.0


def test_monitor_triggers_on_unrealized_drawdown():
    """KLUCZOWA regresja: dla delta-neutral carry realized ≈ 0 przez cały czas
    trzymania — realny drawdown (rozjazd basis) siedzi w UNREALIZED. Guard patrzący
    tylko na realized NIGDY by go nie zauważył. Teraz: limit liczy NETTO."""
    bus = EventBus()
    stops: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))
    Monitor(bus, max_daily_loss_usd=50.0).attach(bus)

    async def run():
        # realized 0, ale unrealized -60 (rozjazd basis na trzymanej parze)
        await bus.publish(Event(EventType.PNL_UPDATE, 1.0, "e",
                                payload=PnLSnapshot(1.0, 0.0, -60.0, 0.0, 0.0)))

    asyncio.run(run())
    assert len(stops) == 1
    assert "NETTO" in stops[0]["reason"]


def test_monitor_daily_loss_resets_on_day_rollover():
    """Limit jest DZIENNY: -40$ wczoraj + -40$ dziś (delta) NIE łamie limitu 50$,
    choć skumulowane -80$ by łamało. Bez rolloveru bot na drugi dzień byłby
    zablokowany wczorajszą stratą."""
    bus = EventBus()
    stops: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))
    Monitor(bus, max_daily_loss_usd=50.0).attach(bus)
    day = 86_400.0

    async def run():
        await bus.publish(Event(EventType.PNL_UPDATE, 10.0, "e",
                                payload=PnLSnapshot(10.0, -40.0, 0.0, 0.0, 0.0)))     # dzień 0: -40
        await bus.publish(Event(EventType.PNL_UPDATE, day + 10.0, "e",
                                payload=PnLSnapshot(day + 10.0, -80.0, 0.0, 0.0, 0.0)))  # dzień 1: delta -40

    asyncio.run(run())
    assert stops == []                       # żadna DOBA nie przekroczyła -50


def test_risk_daily_counters_roll_over():
    """RiskManager: realized_pnl_today i trades_today resetują się na przełomie
    doby — wcześniej reset_day() nie był nigdy wołany, a _on_pnl nadpisywał
    licznik SKUMULOWANYM realized (dzienny limit stawał się limitem od startu)."""
    from backend.core.types import PnLSnapshot as Snap
    from backend.risk import RiskManager
    from tests.test_funding import btc_tick

    rm = RiskManager(_generous(), clock=SimClock())
    bus = EventBus()
    rm.attach(bus)
    day = 86_400.0

    async def run():
        # dzień 0: strata -40 (skumulowana = -40)
        await bus.publish(Event(EventType.PNL_UPDATE, 10.0, "e",
                                payload=Snap(10.0, -40.0, 0.0, 0.0, 0.0)))
        assert rm.realized_pnl_today == -40.0
        # dzień 1: tick przewraca dobę, potem kolejna strata do -70 skumulowanej
        await bus.publish(Event(EventType.MARKET_TICK, day + 5.0, "s",
                                payload=btc_tick(day + 5.0, day + 100.0)))
        assert rm.realized_pnl_today == 0.0          # świeża doba
        await bus.publish(Event(EventType.PNL_UPDATE, day + 10.0, "e",
                                payload=Snap(day + 10.0, -70.0, 0.0, 0.0, 0.0)))
        assert rm.realized_pnl_today == -30.0        # delta dzisiejsza, nie -70

    asyncio.run(run())


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


def test_app_flatten_on_exit_closes_all_pairs():
    """--flatten-on-exit: świadome zejście do flat na końcu sesji — żadna para
    nie zostaje bez nadzoru. Domyślnie OFF (carry trzyma; recovery pilnuje)."""
    app = OneWishApp(mode="synthetic", steps=200, seed=3, gui=False,
                     risk_config=_generous(), notional_usd=200.0, flatten_on_exit=True)
    report = asyncio.run(app.run())
    assert report.counts.get("POSITION_OPENED", 0) > 0     # coś się otwierało
    assert report.open_positions == 0                       # ...i wszystko domknięte

    app_hold = OneWishApp(mode="synthetic", steps=200, seed=3, gui=False,
                          risk_config=_generous(), notional_usd=200.0)
    report_hold = asyncio.run(app_hold.run())
    assert report_hold.open_positions > 0                   # default: carry trzyma


def test_app_synthetic_runs_and_reports():
    app = OneWishApp(mode="synthetic", steps=200, seed=3, gui=False,
                     risk_config=_generous(), notional_usd=200.0)
    report = asyncio.run(app.run())
    assert report.counts.get("MARKET_TICK", 0) > 0
    assert isinstance(report.net, float)
    assert "RAPORT DZIENNY" in report.summary()
    assert report.counts.get("EDGE_DETECTED", 0) > 0


# -- Tier A: wiring funding_weighted przez Pipeline/OneWishApp -------------- #
def test_pipeline_funding_weighted_off_by_default():
    bus = EventBus()
    pipe = Pipeline(bus, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1), clock=SimClock())
    assert pipe.sizer is None
    assert pipe.policy.sizer is None


def test_pipeline_funding_weighted_builds_sizer_from_risk_config():
    bus = EventBus()
    rc = _generous()
    pipe = Pipeline(bus, risk_config=rc, notional_usd=150.0,
                    broker=PaperBrokerAdapter(seed=1), clock=SimClock(),
                    funding_weighted=True)
    assert pipe.sizer is not None
    assert pipe.sizer.base_notional_usd == 150.0
    assert pipe.sizer.max_notional_usd == rc.max_trade_notional_usd
    assert pipe.policy.sizer is pipe.sizer


def test_pipeline_funding_weighted_ignored_outside_carry_mode():
    bus = EventBus()
    pipe = Pipeline(bus, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1),
                    clock=SimClock(), policy_mode="scalp", funding_weighted=True)
    assert pipe.sizer is None       # sizer semantyka funding_bps jest specyficzna dla "carry"


def test_app_funding_weighted_runs_green():
    app = OneWishApp(mode="synthetic", steps=200, seed=3, gui=False,
                     risk_config=_generous(), notional_usd=200.0, funding_weighted=True)
    report = asyncio.run(app.run())
    assert report.counts.get("MARKET_TICK", 0) > 0
    assert isinstance(report.net, float)   # przeszło bez wyjątku z sizerem aktywnym
