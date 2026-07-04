"""Testy bezpieczeństwa: model marginu i watchdog likwidacji nogi short-perp."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import Asset, Fill, Leg, MarketTick, Side, TradeIntent
from backend.execution import PositionBook
from backend.risk.margin import (
    DEFAULT_MAINTENANCE_BY_ASSET,
    MarginModel,
    MarginStressTester,
    MarginWatchdog,
)


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


# -- stress-test marginu (#5) ----------------------------------------------- #
def test_stress_safe_position_reports_safe():
    # short 10@100, lev 3: nawet +30% daje health ~5.1 → bezpiecznie
    tester = MarginStressTester(_book_with_short(), MarginModel(0.005),
                                perp_leverage=3.0, maintenance_by_asset={})
    rep = tester.stress()
    assert "BTC" in rep["positions"]
    rows = rep["positions"]["BTC"]["shocks"]
    assert [r["shock"] for r in rows] == [0.10, 0.20, 0.30]
    healths = [r["health"] for r in rows]
    assert healths[0] > healths[1] > healths[2]            # zdrowie spada z szokiem
    assert rep["summary"]["safe"] is True
    assert rep["summary"]["min_health"] > 1.3
    assert all(rep["summary"]["liquidation_at"][s] == [] for s in (0.10, 0.20, 0.30))


def test_stress_high_leverage_flags_liquidation_at_30():
    # lev 4: +30% → equity ujemny → likwidacja; +10/20% bezpieczne
    tester = MarginStressTester(_book_with_short(), MarginModel(0.005),
                                perp_leverage=4.0, maintenance_by_asset={})
    rep = tester.stress()
    assert rep["summary"]["liquidation_at"][0.30] == ["BTC"]
    assert rep["summary"]["liquidation_at"][0.10] == []
    assert rep["summary"]["liquidation_at"][0.20] == []
    assert rep["summary"]["safe"] is False


def test_stress_flatten_flags_before_liquidation():
    # szeroki flatten_health: +30% (health ~5.1) domknąłby się, ale NIE jest likwidacją
    tester = MarginStressTester(_book_with_short(), MarginModel(0.005),
                                perp_leverage=3.0, flatten_health=6.0,
                                maintenance_by_asset={})
    rep = tester.stress()
    assert rep["summary"]["flatten_at"][0.30] == ["BTC"]
    assert rep["summary"]["liquidation_at"][0.30] == []    # domknięcie ≠ likwidacja
    assert rep["summary"]["safe"] is False


def test_stress_uses_current_marks_not_entry():
    # bieżący mark 120 (już +20% od entry) → szok liczony OD 120, zdrowie niższe
    book = _book_with_short()
    tester = MarginStressTester(book, MarginModel(0.005), perp_leverage=3.0,
                                maintenance_by_asset={})
    at_entry = tester.stress()["positions"]["BTC"]["shocks"][0]["health"]
    at_mark = tester.stress({Asset.BTC: 120.0})
    assert at_mark["positions"]["BTC"]["current_mark"] == 120.0
    assert at_mark["positions"]["BTC"]["shocks"][0]["health"] < at_entry


def test_stress_per_symbol_maintenance_rate_applied():
    book = _book_with_short()
    low = MarginStressTester(book, MarginModel(0.005), perp_leverage=3.0,
                             maintenance_by_asset={Asset.BTC: 0.005}).stress()
    high = MarginStressTester(book, MarginModel(0.005), perp_leverage=3.0,
                              maintenance_by_asset={Asset.BTC: 0.02}).stress()
    assert high["positions"]["BTC"]["maintenance_rate"] == 0.02
    # wyższy maintenance rate → niższe zdrowie przy tym samym szoku
    assert (high["positions"]["BTC"]["shocks"][0]["health"]
            < low["positions"]["BTC"]["shocks"][0]["health"])


def test_stress_default_maintenance_table_used():
    rep = MarginStressTester(_book_with_short(), MarginModel(0.005),
                             perp_leverage=3.0).stress()
    assert DEFAULT_MAINTENANCE_BY_ASSET[Asset.BTC] == 0.004
    assert rep["positions"]["BTC"]["maintenance_rate"] == 0.004   # tabela domyślna


def test_stress_empty_book_is_safe():
    rep = MarginStressTester(PositionBook(), MarginModel(0.005)).stress()
    assert rep["positions"] == {}
    assert rep["summary"]["safe"] is True
    assert rep["summary"]["min_health"] == float("inf")


# -- cross-margin delta-neutral: przeżywalność vs isolated ------------------- #
from backend.risk.margin import DeltaNeutralCrossStress  # noqa: E402


def test_cross_survives_huge_move_isolated_would_not():
    # +146% (jak VELVET): isolated short 3x (próg +31%) = pewna likwidacja;
    # cross (spot pokrywa perp) = przeżywa
    cross = DeltaNeutralCrossStress(perp_leverage=3.0, maintenance_margin_rate=0.02, spot_haircut=0.10)
    assert cross.survives(1.466) is True
    assert cross.equity_ratio(1.466) > 1.0


def test_cross_survives_pure_directional_regardless_of_size():
    cross = DeltaNeutralCrossStress(perp_leverage=3.0, spot_haircut=0.10)
    # bez rozjazdu basis nawet +500% przeżywa (spot rośnie razem z perpem)
    assert cross.survives(5.0, basis_stress=0.0) is True


def test_cross_liquidates_on_extreme_basis_divergence():
    # realnym zabójcą jest basis: perp znacznie ponad spot. Bez haircut-buforu
    # dostatecznie duży rozjazd łamie nawet cross.
    cross = DeltaNeutralCrossStress(perp_leverage=3.0, maintenance_margin_rate=0.02, spot_haircut=0.10)
    assert cross.survives(0.5, basis_stress=0.0) is True
    assert cross.survives(0.5, basis_stress=5.0) is False    # perp +500% ponad spot → śmierć


def test_max_basis_stress_positive_when_survivable():
    cross = DeltaNeutralCrossStress(perp_leverage=3.0, spot_haircut=0.10)
    buf = cross.max_basis_stress(1.0)
    assert buf > 0                                            # jest zapas na squeeze
    assert cross.survives(1.0, buf - 0.05)
    assert not cross.survives(1.0, buf + 0.5)


def test_higher_haircut_reduces_survivability():
    lo = DeltaNeutralCrossStress(perp_leverage=3.0, spot_haircut=0.05)
    hi = DeltaNeutralCrossStress(perp_leverage=3.0, spot_haircut=0.50)
    assert lo.max_basis_stress(1.0) > hi.max_basis_stress(1.0)
