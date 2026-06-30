"""Testy napraw z przeglądu Codexa: atomowość pary, wiring ryzyka, stale-feed,
realny depth, zgodność kontraktu GUI."""
import asyncio

from backend.adapters.exchange import PaperBrokerAdapter
from backend.adapters.market.binance_public import BinancePublicSource
from backend.api.gui_ws import GuiApiServer, event_to_gui
from backend.app.pipeline import Pipeline
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType
from backend.core.types import (
    Asset,
    Leg,
    PnLSnapshot,
    Position,
    RiskDecision,
    Signal,
    SignalState,
    TradeIntent,
)
from backend.monitoring import Monitor
from backend.risk import RiskConfig, RiskManager
from tests.test_repricing import tick_with


def _generous() -> RiskConfig:
    return RiskConfig(max_trade_notional_usd=1000.0, max_asset_exposure_usd=1_000_000.0,
                      max_total_exposure_usd=1e12, max_open_positions=10,
                      max_trades_per_day=10**9, max_spread_bps=100.0, min_depth_usd=0.0)


# -- P1: atomowość dwóch nóg ------------------------------------------------ #
def test_one_leg_rejected_compensates_to_flat():
    bus = EventBus()
    broker = PaperBrokerAdapter(reject_leg=Leg.PERP, seed=1)  # perp zawsze odrzucony
    pipe = Pipeline(bus, risk_config=_generous(), broker=broker, clock=SimClock())

    opened: list = []
    rejected: list = []
    bus.subscribe(EventType.POSITION_OPENED, lambda e: opened.append(e.payload))
    bus.subscribe(EventType.ORDER_REJECTED, lambda e: rejected.append(e.payload))

    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 1.0, "s", payload=tick_with(60.0))))

    assert opened == []                                   # nie otwieramy niepełnej pary
    assert any("kompensacja" in (r.get("reason", "")) for r in rejected)
    assert not pipe.book.is_open(Asset.BTC)               # wróciliśmy do flat
    pos = pipe.book.position(Asset.BTC)
    if pos is not None:
        assert abs(pos.net_delta) < 1e-9                  # zero ekspozycji kierunkowej


# -- P1: wiring RiskManagera do realnej książki ----------------------------- #
def test_risk_tracks_exposure_from_positions():
    bus = EventBus()
    rm = RiskManager(_generous(), clock=SimClock())
    rm.attach(bus)
    pos = Position(id="p", asset=Asset.BTC, spot_qty=0.01, spot_entry=60_000.0,
                   perp_qty=-0.01, perp_entry=60_000.0)

    asyncio.run(bus.publish(Event(EventType.POSITION_OPENED, 1.0, "e", payload=pos)))
    assert rm.exposure.get(Asset.BTC, 0.0) > 0            # ~600$ nominału
    assert rm.open_positions == 1
    assert rm.trades_today == 1

    asyncio.run(bus.publish(Event(EventType.POSITION_CLOSED, 2.0, "e", payload=pos)))
    assert rm.open_positions == 0


def test_risk_tracks_daily_loss_from_pnl():
    bus = EventBus()
    rm = RiskManager(RiskConfig(max_daily_loss_usd=50.0), clock=SimClock())
    rm.attach(bus)
    asyncio.run(bus.publish(Event(EventType.PNL_UPDATE, 1.0, "e",
                                  payload=PnLSnapshot(1.0, -60.0, 0.0, 0.0, 0.0))))
    assert rm.realized_pnl_today == -60.0
    # i teraz nowe wejście jest blokowane limitem dziennej straty
    d = rm.approve(TradeIntent(Asset.BTC, 1.0, "OPEN", 100.0, 5.0), tick_with(60.0))
    assert not d.approved and "limit straty" in d.reason


# -- P2: stale-feed → EMERGENCY_STOP ---------------------------------------- #
def test_monitor_emergency_on_stale_feed():
    bus = EventBus()
    stops: list = []
    bus.subscribe(EventType.EMERGENCY_STOP, lambda e: stops.append(e.payload))
    mon = Monitor()
    mon.attach(bus)
    asyncio.run(bus.publish(Event(EventType.STALE_FEED, 1.0, "adapter", payload={"gap_s": 12.0})))
    assert len(stops) == 1
    assert "feed" in stops[0]["reason"]


# -- P1: realny depth ------------------------------------------------------- #
def test_binance_depth_usd_sums_top_levels():
    src = BinancePublicSource(depth_levels=2)
    levels = [["100", "2"], ["99", "3"], ["98", "10"]]
    assert src._depth_usd(levels) == 100 * 2 + 99 * 3   # 497, tylko top 2


# -- P1: zgodność kontraktu GUI --------------------------------------------- #
def test_gui_position_margin_ratio_is_null():
    pos = Position(id="p", asset=Asset.BTC, spot_qty=1.0, spot_entry=100.0,
                   perp_qty=-1.0, perp_entry=100.0)
    msg = event_to_gui(Event(EventType.POSITION_OPENED, 1.0, "e", payload=pos))
    assert msg["marginRatio"] is None                    # paper nie modeluje marginu


def test_gui_signal_series_and_risk_decision_value():
    bus = EventBus()
    rm = RiskManager(_generous(), clock=SimClock())
    server = GuiApiServer(bus, risk=rm)
    sig = Signal(Asset.BTC, 1.0, SignalState.EDGE_DETECTED, 10.0, 2.0, 8.0, 5.0, 20.0, "x")

    async def run():
        await server._on_event(Event(EventType.EDGE_DETECTED, 1.0, "d", payload=sig))
        await server._on_event(Event(EventType.EDGE_DETECTED, 2.0, "d", payload=sig))
        dec = RiskDecision(approved=False, ts=1.0, reason="limit")
        intent = TradeIntent(Asset.BTC, 1.0, "OPEN", 200.0, 5.0)
        await server._on_event(Event(EventType.RISK_REJECTED, 1.0, "risk",
                                     payload={"intent": intent, "decision": dec}))

    asyncio.run(run())
    assert len(server._signal_series["BTC"]) == 2        # seria do wykresu fair-basis
    assert server._last_risk["decision"] == "RISK_REJECTED"
