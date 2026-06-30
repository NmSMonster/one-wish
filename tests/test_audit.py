"""Testy M8: AuditTrail — odtwarzanie „dlaczego wszedł / nie wszedł"."""
import asyncio

from backend.core.bus import EventBus
from backend.core.events import Event, EventType
from backend.core.types import (
    Asset,
    Fill,
    Leg,
    RiskDecision,
    Side,
    Signal,
    SignalState,
    TradeIntent,
)
from backend.storage import AuditTrail, Database


def _signal(asset, state, reason):
    return Signal(asset=asset, ts=10.0, state=state, observed_basis_bps=5.0,
                  fair_basis_bps=2.0, dislocation_bps=3.0, expected_net_edge_bps=-1.0,
                  cost_bps=20.0, reason=reason)


def test_explain_no_trade_reports_reason():
    bus = EventBus()
    db = Database()
    db.attach(bus)

    async def run():
        await bus.publish(Event(EventType.NO_TRADE_CONDITION, 10.0, "det",
                                payload=_signal(Asset.BTC, SignalState.NO_TRADE,
                                                "za mała dyslokacja")))

    asyncio.run(run())
    text = AuditTrail(db).explain("BTC", 10.0)
    assert "BTC" in text
    assert "za mała dyslokacja" in text
    assert "brak wejścia" in text
    db.close()


def test_explain_entry_chain():
    bus = EventBus()
    db = Database()
    db.attach(bus)

    intent = TradeIntent(Asset.ETH, 20.0, "OPEN", 200.0, 8.0, "edge")
    decision = RiskDecision(approved=True, ts=20.0, reason="OK")

    async def run():
        await bus.publish(Event(EventType.EDGE_DETECTED, 20.0, "det",
                                payload=_signal(Asset.ETH, SignalState.EDGE_DETECTED, "edge +8")))
        await bus.publish(Event(EventType.TRADE_INTENT, 20.0, "strat", payload=intent))
        await bus.publish(Event(EventType.RISK_APPROVED, 20.0, "risk",
                                payload={"intent": intent, "decision": decision}))
        await bus.publish(Event(EventType.FILL, 20.0, "exec",
                                payload=Fill("c1", Asset.ETH, Leg.SPOT, Side.BUY,
                                             2000.0, 0.1, 0.15, 20.0)))

    asyncio.run(run())
    audit = AuditTrail(db)
    text = audit.explain("ETH", 20.0)
    assert "EDGE_DETECTED" in text
    assert "APPROVED" in text
    assert "WSZEDŁ" in text

    tl = audit.timeline(asset="ETH")
    assert len(tl) == 4
    assert audit.summary()[EventType.FILL.value] == 1
    db.close()
