"""Testy M7: StrategyPolicy — dedupe wejść i zamykanie na EDGE_LOST."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import Asset, Position, Signal, SignalState, TradeIntent
from backend.strategy import FundingWeightedSizer, StrategyPolicy
from tests.test_repricing import tick_with


def _signal(asset=Asset.BTC, state=SignalState.EDGE_DETECTED, edge_bps=5.0) -> Signal:
    return Signal(asset=asset, ts=1.0, state=state, observed_basis_bps=10.0,
                  fair_basis_bps=2.0, dislocation_bps=8.0, expected_net_edge_bps=edge_bps,
                  cost_bps=20.0, reason="x")


def _position(asset=Asset.BTC) -> Position:
    return Position(id="p", asset=asset, spot_qty=1.0, perp_qty=-1.0, opened_ts=1.0)


def test_policy_dedupes_open_and_closes_on_edge_lost():
    bus = EventBus()
    policy = StrategyPolicy(notional_usd=200.0, mode="scalp")
    policy.attach(bus)

    intents: list[TradeIntent] = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    async def run():
        # dwa EDGE pod rząd → tylko jeden OPEN (dedupe „w locie")
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "det", payload=_signal()))
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "det", payload=_signal()))
        # pozycja otwarta
        await bus.publish(Event(EventType.POSITION_OPENED, 1.0, "exec", payload=_position()))
        # kolejny EDGE gdy trzymamy → brak nowego OPEN
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "det", payload=_signal()))
        # edge znika → CLOSE
        await bus.publish(Event(EventType.EDGE_LOST, 1.0, "det", payload=_signal()))
        # po zamknięciu znowu można otworzyć
        await bus.publish(Event(EventType.POSITION_CLOSED, 1.0, "exec", payload=_position()))
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "det", payload=_signal()))

    asyncio.run(run())

    actions = [i.action for i in intents]
    assert actions == ["OPEN", "CLOSE", "OPEN"]


def test_policy_ignores_edge_lost_when_not_holding():
    bus = EventBus()
    policy = StrategyPolicy(mode="scalp")
    policy.attach(bus)
    intents: list = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    asyncio.run(bus.publish(Event(EventType.EDGE_LOST, 1.0, "det", payload=_signal())))
    assert intents == []  # nie trzymamy → nie ma czego zamykać


def test_carry_holds_through_edge_lost_and_exits_on_funding_flip():
    bus = EventBus()
    policy = StrategyPolicy(mode="carry", funding_ema_alpha=1.0)  # bez wygładzania (instant)
    policy.attach(bus)
    intents: list = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    async def run():
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "d", payload=_signal()))       # OPEN
        await bus.publish(Event(EventType.POSITION_OPENED, 1.0, "e", payload=_position()))
        # carry: zanik dyslokacji NIE zamyka
        await bus.publish(Event(EventType.EDGE_LOST, 2.0, "d",
                                payload=_signal(state=SignalState.EDGE_LOST)))
        # funding wciąż dodatni → trzymamy
        await bus.publish(Event(EventType.MARKET_TICK, 3.0, "s",
                                payload=tick_with(5.0, funding=0.0002)))
        # funding ujemny → CLOSE (nośność znikła)
        await bus.publish(Event(EventType.MARKET_TICK, 4.0, "s",
                                payload=tick_with(5.0, funding=-0.0001)))

    asyncio.run(run())
    assert [i.action for i in intents] == ["OPEN", "CLOSE"]


def test_carry_smoothed_holds_through_single_negative_dip():
    # z wygładzaniem (alpha mały) pojedynczy ujemny funding NIE zamyka — to fix na churn
    bus = EventBus()
    policy = StrategyPolicy(mode="carry", funding_ema_alpha=0.1)
    policy.attach(bus)
    intents: list = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    async def run():
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "d", payload=_signal()))
        await bus.publish(Event(EventType.POSITION_OPENED, 1.0, "e", payload=_position()))
        for i in range(5):  # rozgrzej EMA dodatnim funding
            await bus.publish(Event(EventType.MARKET_TICK, 2.0 + i, "s",
                                    payload=tick_with(5.0, funding=0.0003)))
        # jeden ujemny dip — EMA pozostaje dodatnia → BRAK zamknięcia
        await bus.publish(Event(EventType.MARKET_TICK, 9.0, "s",
                                payload=tick_with(5.0, funding=-0.0001)))

    asyncio.run(run())
    assert [i.action for i in intents] == ["OPEN"]   # tylko otwarcie, dip nie wyrzucił


def test_carry_exits_on_basis_stop():
    bus = EventBus()
    policy = StrategyPolicy(mode="carry", basis_stop_bps=-10.0)
    policy.attach(bus)
    intents: list = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    async def run():
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "d", payload=_signal()))
        await bus.publish(Event(EventType.POSITION_OPENED, 1.0, "e", payload=_position()))
        # basis -20 ≤ stop -10 → CLOSE (perp za tani, short traci na basis)
        await bus.publish(Event(EventType.MARKET_TICK, 2.0, "s",
                                payload=tick_with(-20.0, funding=0.0002)))

    asyncio.run(run())
    assert [i.action for i in intents] == ["OPEN", "CLOSE"]


# -- Tier A: sizer waży notional_usd siłą sygnału --------------------------- #
def test_without_sizer_uses_flat_notional():
    bus = EventBus()
    policy = StrategyPolicy(notional_usd=200.0, mode="carry")
    policy.attach(bus)
    intents: list[TradeIntent] = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    asyncio.run(bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "d",
                                  payload=_signal(edge_bps=2.53))))
    assert intents[0].notional_usd == 200.0    # bez sizera: zawsze płaski nominał


def test_with_sizer_notional_scales_with_edge():
    bus = EventBus()
    sizer = FundingWeightedSizer(base_notional_usd=100.0, ref_funding_bps=1.5,
                                 min_mult=0.5, max_mult=2.0)
    policy = StrategyPolicy(notional_usd=100.0, mode="carry", sizer=sizer)
    policy.attach(bus)
    intents: list[TradeIntent] = []
    bus.subscribe(EventType.TRADE_INTENT, lambda e: intents.append(e.payload))

    async def run():
        await bus.publish(Event(EventType.EDGE_DETECTED, 1.0, "d",
                                payload=_signal(asset=Asset.BTC, edge_bps=0.96)))
        await bus.publish(Event(EventType.POSITION_OPENED, 1.0, "e", payload=_position(Asset.BTC)))
        await bus.publish(Event(EventType.EDGE_DETECTED, 2.0, "d",
                                payload=_signal(asset=Asset.ETH, edge_bps=2.53)))
    asyncio.run(run())

    btc_notional = intents[0].notional_usd
    eth_notional = intents[1].notional_usd
    assert btc_notional < 100.0 < eth_notional     # niski funding mniej kapitału, wysoki więcej
    assert abs(btc_notional - sizer.size(0.96)) < 1e-9
    assert abs(eth_notional - sizer.size(2.53)) < 1e-9
