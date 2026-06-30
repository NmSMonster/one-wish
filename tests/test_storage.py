"""Testy M3/M8: Storage (sqlite) — audit trail i typowane zapisy."""
import asyncio
import json

from backend.adapters.market import MarketDataAdapter, SyntheticSource
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import Event, EventType, Severity
from backend.core.types import Asset, Fill, Leg, PnLSnapshot, Side
from backend.storage import Database


def test_db_records_ticks_and_events():
    bus = EventBus()
    db = Database()
    db.attach(bus)

    src = SyntheticSource(steps=10)
    adapter = MarketDataAdapter(src, bus, SimClock(), lag_warn_ms=99_999)
    n = asyncio.run(adapter.run())

    assert db.count("ticks") == n
    assert db.count("events") == n          # 1 event MARKET_TICK na tick
    assert len(db.ticks_for("BTC")) == 10
    db.close()


def test_db_typed_inserts():
    db = Database()
    db.record_fill(Fill("c1", Asset.BTC, Leg.SPOT, Side.BUY, 60_000.0, 0.01, 0.6, 1.0))
    db.record_pnl(PnLSnapshot(ts=1.0, realized=10.0, unrealized=5.0,
                              funding_collected=2.0, fees_paid=1.0))
    assert db.count("fills") == 1
    assert db.count("pnl") == 1
    db.close()


def test_db_event_payload_serialized():
    bus = EventBus()
    db = Database()
    db.attach(bus)
    asyncio.run(bus.publish(
        Event(EventType.RISK_REJECTED, 1.0, "risk", Severity.WARNING,
              payload={"reason": "limit dzienny"})
    ))
    evs = db.recent_events(event_type=EventType.RISK_REJECTED)
    assert len(evs) == 1
    assert json.loads(evs[0]["payload"])["reason"] == "limit dzienny"
    db.close()


# -- chaos #7: awaria zapisu DB nie może zabić bota ------------------------- #
def test_db_write_failure_is_isolated_and_does_not_crash_bus():
    import sqlite3

    bus = EventBus()
    db = Database()
    db.attach(bus)

    # po inicjalizacji schematu psujemy każdy zapis (np. dysk pełny / lock).
    # sqlite3.Connection.execute jest read-only, więc podmieniamy całe połączenie.
    class _BoomConn:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("symulowana awaria dysku")

        def commit(self):
            pass
    db._conn = _BoomConn()

    received: list = []
    bus.subscribe(EventType.MARKET_TICK, lambda e: received.append(e))   # inny subskrybent

    # publish nie może wyrzucić wyjątku mimo padającego zapisu DB
    asyncio.run(bus.publish(Event(EventType.MARKET_TICK, 1.0, "s", payload={"x": 1})))

    assert received                       # reszta szyny działa mimo awarii DB (izolacja)
