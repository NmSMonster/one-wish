"""Testy M6: RiskManager — każda blokada osobno (moduł krytyczny)."""
import asyncio

from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType
from backend.core.types import Asset, TradeIntent
from backend.risk import RiskConfig, RiskManager
from tests.test_repricing import tick_with


def intent(asset=Asset.BTC, notional=100.0, action="OPEN") -> TradeIntent:
    return TradeIntent(asset=asset, ts=1.0, action=action, notional_usd=notional,
                       expected_net_edge_bps=10.0, reason="test")


def mgr(**overrides) -> RiskManager:
    cfg = RiskConfig(**overrides)
    return RiskManager(cfg, clock=SimClock(1.0))


def test_approves_within_limits():
    d = mgr().approve(intent(notional=100.0), tick_with(50.0))
    assert d.approved


def test_daily_loss_limit_blocks():
    m = mgr(max_daily_loss_usd=50.0)
    m.set_realized_pnl_today(-50.0)
    d = m.approve(intent(), tick_with(50.0))
    assert not d.approved and "limit straty" in d.reason


def test_trade_notional_limit_blocks():
    d = mgr(max_trade_notional_usd=250.0).approve(intent(notional=300.0), tick_with(50.0))
    assert not d.approved and "nominał" in d.reason


def test_asset_exposure_limit_blocks():
    m = mgr(max_asset_exposure_usd=500.0)
    m.register_open(Asset.BTC, 450.0)
    d = m.approve(intent(asset=Asset.BTC, notional=100.0), tick_with(50.0))
    assert not d.approved and "ekspozycja" in d.reason


def test_total_exposure_limit_blocks():
    # asset ≤ total (spójna konfiguracja): ETH 350 + BTC 100 = 450 > total 400,
    # ale każde z osobna ≤ limit na aktywo → blokuje DOPIERO łączny limit
    m = mgr(max_total_exposure_usd=400.0, max_asset_exposure_usd=400.0)
    m.register_open(Asset.ETH, 350.0)
    d = m.approve(intent(asset=Asset.BTC, notional=100.0), tick_with(50.0))
    assert not d.approved and "łączna ekspozycja" in d.reason


def test_max_open_positions_blocks_new_asset():
    m = mgr(max_open_positions=2, max_asset_exposure_usd=10_000.0,
            max_total_exposure_usd=10_000.0)
    m.register_open(Asset.BTC, 100.0)
    m.register_open(Asset.ETH, 100.0)
    d = m.approve(intent(asset=Asset.SOL, notional=100.0), tick_with(50.0))
    assert not d.approved and "otwartych pozycji" in d.reason
    # ale dokładanie do istniejącego aktywa wolno
    assert m.approve(intent(asset=Asset.BTC, notional=50.0), tick_with(50.0)).approved


def test_max_trades_per_day_blocks():
    m = mgr(max_trades_per_day=1)
    m.register_open(Asset.BTC, 10.0)  # trades_today = 1
    m.register_close(Asset.BTC)
    d = m.approve(intent(asset=Asset.ETH, notional=10.0), tick_with(50.0))
    assert not d.approved and "transakcji/dzień" in d.reason


def test_wide_spread_blocks():
    d = mgr().approve(intent(), tick_with(50.0, perp_spread_bps=30.0))
    assert not d.approved and "spread" in d.reason


def test_thin_liquidity_blocks():
    d = mgr().approve(intent(), tick_with(50.0, depth=10_000.0))
    assert not d.approved and "płynność" in d.reason


def test_kill_switch_blocks_open_allows_close():
    m = mgr()
    m.kill("test awaryjny")
    assert not m.approve(intent(action="OPEN"), tick_with(50.0)).approved
    assert m.approve(intent(action="CLOSE"), tick_with(50.0)).approved


def test_close_always_allowed():
    assert mgr().approve(intent(action="CLOSE"), tick_with(50.0)).approved


def test_config_from_yaml(tmp_path):
    p = tmp_path / "risk.yaml"
    p.write_text("max_daily_loss_usd: 12.5\nlive_trading_enabled: false\nunknown_key: 1\n",
                 encoding="utf-8")
    cfg = RiskConfig.from_yaml(str(p))
    assert cfg.max_daily_loss_usd == 12.5
    assert cfg.live_trading_enabled is False


def test_attach_emits_risk_events():
    bus = EventBus()
    m = RiskManager(RiskConfig(max_trade_notional_usd=250.0), clock=SimClock(1.0))
    m.attach(bus)
    seen: list = []
    for et in (EventType.RISK_APPROVED, EventType.RISK_REJECTED):
        bus.subscribe(et, lambda e: seen.append(e.type))

    async def run():
        await bus.publish(Event(EventType.MARKET_TICK, 1.0, "t", payload=tick_with(50.0)))
        await bus.publish(Event(EventType.TRADE_INTENT, 1.0, "strat", payload=intent(notional=100.0)))
        await bus.publish(Event(EventType.TRADE_INTENT, 1.0, "strat", payload=intent(notional=999.0)))

    asyncio.run(run())
    assert EventType.RISK_APPROVED in seen
    assert EventType.RISK_REJECTED in seen


def test_emergency_stop_triggers_kill():
    bus = EventBus()
    m = RiskManager(RiskConfig(), clock=SimClock(1.0))
    m.attach(bus)
    asyncio.run(bus.publish(Event(EventType.EMERGENCY_STOP, 1.0, "mon",
                                  payload={"reason": "stale feed"})))
    assert m.is_killed


# -- walidacja konfiguracji (fail-fast na starcie) -------------------------- #
def _bad(**kw):
    import pytest
    with pytest.raises(ValueError):
        RiskConfig(**kw)


def test_config_rejects_nonpositive_limits():
    _bad(max_daily_loss_usd=0.0)          # brak limitu straty = katastrofa
    _bad(max_daily_loss_usd=-50.0)        # literówka ze znakiem
    _bad(max_trade_notional_usd=0.0)
    _bad(perp_leverage=0.0)
    _bad(max_open_positions=0)


def test_config_rejects_excessive_leverage():
    _bad(perp_leverage=30.0)              # short likwiduje się przy ~5%
    RiskConfig(perp_leverage=20.0)        # granica dozwolona


def test_config_rejects_inconsistent_exposure_ladder():
    # transakcja > na aktywo
    _bad(max_trade_notional_usd=600.0, max_asset_exposure_usd=500.0,
         max_total_exposure_usd=1500.0)
    # na aktywo > łączne
    _bad(max_asset_exposure_usd=2000.0, max_total_exposure_usd=1500.0)


def test_config_rejects_bad_margin_health_thresholds():
    _bad(margin_flatten_health=0.9)                       # flatten pod likwidacją (1.0)
    _bad(margin_warn_health=1.2, margin_flatten_health=1.4)  # warn < flatten


def test_config_from_yaml_validates(tmp_path):
    import pytest
    p = tmp_path / "bad.yaml"
    p.write_text("perp_leverage: 50\nmax_daily_loss_usd: -10\n", encoding="utf-8")
    with pytest.raises(ValueError):
        RiskConfig.from_yaml(str(p))


def test_default_and_shipped_config_are_valid():
    RiskConfig()                                          # domyślna spójna
    import os
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "risk_config.yaml")
    if os.path.exists(yaml_path):
        RiskConfig.from_yaml(yaml_path)                   # dostarczony plik też
