"""Testy bezpieczeństwa: model marginu i watchdog likwidacji nogi short-perp."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import Asset, Fill, Leg, MarketTick, Side, TradeIntent
from backend.execution import PositionBook
from backend.risk.margin import MarginModel, MarginWatchdog


def _tick(perp, mark=None) -> MarketTick:
    return MarketTick(asset=Asset.BTC, ts=2.0, spot=perp, perp=perp, index=perp,
                      funding_rate=0.0, predicted_funding=0.0, next_funding_ts=3.0,
                      spot_bid=perp - 1, spot_ask=perp + 1, perp_bid=perp - 1, perp_ask=perp + 1,
                      spot_depth_usd=1e6, perp_depth_usd=1e6,
                      mark_price=(perp if mark is None else mark))


# -- model matematyczny ----------------------------------------------------- #
def test_required_margin_and_max_leverage():
    m = MarginModel(maintenance_margin_rate=0.005)
    assert abs(m.required_margin(1000.0, 0.30) - 305.0) < 1e-9
    assert abs(m.max_safe_leverage(0.30) - 1.0 / 0.305) < 1e-9


def test_liquidation_price_short():
    m = MarginModel(maintenance_margin_rate=0.005)
    # short, lev 3: liq ~ +32.83% nad entry
    assert abs(m.liquidation_price_short(100.0, 3.0) - 132.83) < 1e-2


def test_health_short_safe_and_danger():
    m = MarginModel(maintenance_margin_rate=0.005)
    posted = 1000.0 / 3.0
    assert m.health_short(100.0, 100.0, 1000.0, posted) > 50      # brak ruchu = bardzo bezpiecznie
    assert m.health_short(100.0, 133.0, 1000.0, posted) < 1.0     # +33% → strefa likwidacji


# -- watchdog --------------------------------------------------------------- #
def _book_with_short(qty=10.0, entry=100.0) -> PositionBook:
    book = PositionBook()
    book.apply_fill(Fill("s", Asset.BTC, Leg.SPOT, Side.BUY, entry, qty, 0.0, 1.0), 1.0)
    book.apply_fill(Fill("p", Asset.BTC, Leg.PERP, Side.SELL, entry, qty, 0.0, 1.0), 1.0)
    return book


def test_watchdog_flattens_before_liquidation():
    book = _book_with_short()
    bus = EventBus()
    wd = MarginWatchdog(book, MarginModel(0.005), perp_leverage=3.0,
                        warn_health=1.8, flatten_health=1.3)
    wd.attach(bus)
    intents: list = []
    warnings: list = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))
    bus.subscribe(EventType.MARGIN_WARNING, lambda e: warnings.append(e.payload))

    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 2.0, "s", payload=_tick(133.0))))

    assert any(isinstance(i, TradeIntent) and i.action == "CLOSE" and i.asset == Asset.BTC
               for i in intents)
    assert warnings and warnings[-1]["action"] == "flatten" and warnings[-1]["health"] < 1.3


def test_watchdog_silent_when_safe():
    book = _book_with_short()
    bus = EventBus()
    MarginWatchdog(book, MarginModel(0.005), perp_leverage=3.0).attach(bus)
    events: list = []
    bus.subscribe(EventType.MARGIN_WARNING, lambda e: events.append(e))
    bus.subscribe(EventType.TRADE_INTENT, lambda e: events.append(e))

    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 2.0, "s", payload=_tick(100.5))))
    assert events == []          # mały ruch → cisza


def test_watchdog_warn_zone_emits_warning_only():
    book = _book_with_short()
    bus = EventBus()
    wd = MarginWatchdog(book, MarginModel(0.005), perp_leverage=3.0,
                        warn_health=5.0, flatten_health=1.3)   # szeroka strefa warn dla testu
    wd.attach(bus)
    intents: list = []
    warnings: list = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))
    bus.subscribe(EventType.MARGIN_WARNING, lambda e: warnings.append(e.payload))

    # +30% → health ~5.1: między flatten(1.3) a warn(5.0)? health 5.13 > 5.0 → ok; uzyjmy +31%
    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 2.0, "s", payload=_tick(131.0))))
    assert warnings and warnings[-1]["action"] == "warn"
    assert not any(i.action == "CLOSE" for i in intents)   # warn nie zamyka
