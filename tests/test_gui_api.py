"""Testy M9: GUI API — translacja kontraktu i round-trip WebSocket."""
import asyncio
import json

import pytest
import websockets

from backend.api.gui_ws import GuiApiServer, event_to_gui
from backend.core.bus import EventBus
from backend.core.events import Event, EventType, Severity
from backend.core.types import (
    Asset,
    Fill,
    Leg,
    OrderRequest,
    OrderType,
    PnLSnapshot,
    Position,
    Side,
    Signal,
    SignalState,
    TradeIntent,
)
from tests.test_repricing import tick_with


# -- translacja (czysta funkcja) ------------------------------------------- #
def test_translate_market():
    msg = event_to_gui(Event(EventType.MARKET_TICK, 1.0, "s", payload=tick_with(10.0)))
    assert msg["type"] == "market" and msg["asset"] == "BTC"
    assert msg["basisBps"] == pytest.approx(10.0, abs=0.1)


def test_translate_signal():
    sig = Signal(Asset.ETH, 1.0, SignalState.EDGE_DETECTED, 10.0, 2.0, 8.0, 5.0, 20.0, "edge")
    msg = event_to_gui(Event(EventType.EDGE_DETECTED, 1.0, "d", payload=sig))
    assert msg["type"] == "signal" and msg["state"] == "EDGE_DETECTED"
    assert msg["expectedNetEdge"] == 5.0


def test_translate_position_with_marks():
    pos = Position(id="p", asset=Asset.BTC, spot_qty=1.0, spot_entry=100.0,
                   perp_qty=-1.0, perp_entry=100.0)
    msg = event_to_gui(Event(EventType.POSITION_OPENED, 1.0, "e", payload=pos),
                       marks={Asset.BTC: (110.0, 110.0)})
    assert msg["type"] == "position"
    assert msg["netDelta"] == pytest.approx(0.0)
    assert msg["unrealizedPnl"] == pytest.approx(0.0)  # delta-neutral


def test_translate_order_and_fill():
    o = event_to_gui(Event(EventType.ORDER_REQUEST, 1.0, "e",
                     payload=OrderRequest("c1", Asset.BTC, Leg.SPOT, Side.BUY,
                                          OrderType.MARKET, 100.0, 1.0, 1.0)))
    assert o["type"] == "order" and o["leg"] == "SPOT"
    f = event_to_gui(Event(EventType.FILL, 1.0, "e",
                     payload=Fill("c1", Asset.BTC, Leg.SPOT, Side.BUY, 100.0, 1.0, 0.1, 1.0)))
    assert f["type"] == "fill" and f["orderId"] == "c1"


def test_translate_pnl_and_audit():
    p = event_to_gui(Event(EventType.PNL_UPDATE, 1.0, "e",
                     payload=PnLSnapshot(1.0, 10.0, 5.0, 2.0, 1.0)))
    assert p["type"] == "pnl" and p["net"] == pytest.approx(16.0)
    a = event_to_gui(Event(EventType.RISK_REJECTED, 1.0, "risk", Severity.WARNING,
                           payload={"reason": "limit"}))
    assert a["type"] == "event" and "limit" in a["message"]


def test_translate_returns_none_for_internal_events():
    assert event_to_gui(Event(EventType.TRADE_INTENT, 1.0, "s",
                              payload=TradeIntent(Asset.BTC, 1.0, "OPEN", 200.0, 5.0))) is None


# -- round-trip WebSocket --------------------------------------------------- #
def test_gui_server_roundtrip():
    async def scenario():
        bus = EventBus()
        server = GuiApiServer(bus, host="127.0.0.1", port=8766, connection="SIMULATION")
        try:
            await server.start()
        except OSError as exc:  # brak możliwości bindowania w środowisku
            pytest.skip(f"nie można uruchomić serwera WS: {exc}")

        commands: list = []
        bus.subscribe(EventType.OPERATOR_COMMAND, lambda e: commands.append(e.payload))
        kills: list = []
        bus.subscribe(EventType.KILL_SWITCH, lambda e: kills.append(e.payload))

        try:
            async with websockets.connect("ws://127.0.0.1:8766/gui") as ws:
                snapshot = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
                assert snapshot["type"] == "status"

                await bus.publish(Event(EventType.MARKET_TICK, 1.0, "s", payload=tick_with(12.0)))
                got_market = None
                for _ in range(15):
                    m = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
                    if m["type"] == "market":
                        got_market = m
                        break
                assert got_market and got_market["asset"] == "BTC"

                await ws.send(json.dumps({"type": "command", "action": "kill"}))
                await asyncio.sleep(0.2)
        finally:
            await server.stop()

        assert any(c.get("action") == "kill" for c in commands)
        assert len(kills) == 1

    asyncio.run(scenario())
