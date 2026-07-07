"""GuiApiServer (M9) — most między szyną eventów a GUI przez WebSocket.

Tłumaczy eventy backendu na wiadomości kontraktu GUI (status/market/signal/
position/order/fill/pnl/risk/event) i rozsyła je do podłączonych klientów.
Przyjmuje WYŁĄCZNIE jawne komendy operatora (pause/resume/kill/flatten) i zamienia
je na eventy szyny — GUI nie podejmuje żadnych decyzji tradingowych.

Adres zgodny z GUI Codexa: ws://127.0.0.1:8765/gui
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging

import websockets

from ..core.bus import EventBus
from ..core.clock import Clock, RealClock
from ..core.events import Event, EventType
from ..core.serialize import to_jsonable
from ..core.types import (
    Fill,
    MarketTick,
    OrderRequest,
    PnLSnapshot,
    Position,
    Signal,
)

log = logging.getLogger("onewish.gui")

_SIGNAL_EVENTS = {EventType.EDGE_DETECTED, EventType.EDGE_LOST, EventType.NO_TRADE_CONDITION}
_POSITION_EVENTS = {EventType.POSITION_OPENED, EventType.POSITION_UPDATED, EventType.POSITION_CLOSED}
_ORDER_EVENTS = {EventType.ORDER_REQUEST, EventType.ORDER_UPDATE}
_FILL_EVENTS = {EventType.FILL, EventType.PARTIAL_FILL}
_AUDIT_EVENTS = {
    EventType.DATA_LAG_WARNING, EventType.STALE_FEED, EventType.EMERGENCY_STOP,
    EventType.KILL_SWITCH, EventType.ORDER_REJECTED, EventType.RISK_REJECTED,
    EventType.RISK_APPROVED, EventType.RISK_LIMIT_BREACH, EventType.MARGIN_WARNING,
}


def _event_message(event: Event) -> str:
    p = event.payload
    if isinstance(p, dict) and "reason" in p:
        return f"{event.type.value}: {p['reason']}"
    return event.type.value


def event_to_gui(event: Event, marks: dict | None = None) -> dict | None:
    """Czysta translacja eventu na wiadomość kontraktu GUI (lub None)."""
    t = event.type
    p = event.payload

    if t == EventType.MARKET_TICK and isinstance(p, MarketTick):
        return {"type": "market", "asset": p.asset.value, "spot": p.spot, "perp": p.perp,
                "index": p.index, "basisBps": p.basis_bps, "fundingRate": p.funding_rate,
                "predictedFunding": p.predicted_funding, "nextFundingTs": p.next_funding_ts,
                "dataLagMs": p.data_lag_ms}

    if t in _SIGNAL_EVENTS and isinstance(p, Signal):
        return {"type": "signal", "asset": p.asset.value, "fairBasisBps": p.fair_basis_bps,
                "observedBasisBps": p.observed_basis_bps, "dislocationBps": p.dislocation_bps,
                "expectedNetEdge": p.expected_net_edge_bps, "state": p.state.value,
                "reason": p.reason}

    if t in _POSITION_EVENTS and isinstance(p, Position):
        mark = (marks or {}).get(p.asset, (p.spot_entry, p.perp_entry))
        return {"type": "position", "id": p.id, "asset": p.asset.value, "spotQty": p.spot_qty,
                "spotEntry": p.spot_entry, "perpQty": p.perp_qty, "perpEntry": p.perp_entry,
                "netDelta": p.net_delta, "unrealizedPnl": p.price_pnl(mark[0], mark[1]),
                "fundingAccrued": p.funding_accrued, "marginRatio": None, "openedTs": p.opened_ts,
                # GUI musi wiedzieć, że pozycja jest ZAMKNIĘTA (usuwa z tabeli) —
                # POSITION_CLOSED bez tej flagi wyglądał identycznie jak otwarcie.
                "closed": t == EventType.POSITION_CLOSED or p.closed_ts is not None}

    if t in _ORDER_EVENTS and isinstance(p, OrderRequest):
        return {"type": "order", "id": p.client_order_id, "asset": p.asset.value,
                "leg": p.leg.value, "side": p.side.value, "orderType": p.order_type.value,
                "price": p.price, "qty": p.qty, "status": "NEW"}

    if t in _FILL_EVENTS and isinstance(p, Fill):
        return {"type": "fill", "orderId": p.client_order_id, "asset": p.asset.value,
                "leg": p.leg.value, "price": p.price, "qty": p.qty, "fee": p.fee, "ts": p.ts}

    if t == EventType.PNL_UPDATE and isinstance(p, PnLSnapshot):
        return {"type": "pnl", "realized": p.realized, "unrealized": p.unrealized,
                "fundingCollected": p.funding_collected, "feesPaid": p.fees_paid,
                "net": p.net, "series": []}

    if t in _AUDIT_EVENTS:
        data = {} if isinstance(p, MarketTick) else to_jsonable(p)
        return {"type": "event", "ts": event.ts, "eventType": t.value,
                "severity": event.severity.value, "message": _event_message(event),
                "data": data}

    return None


class GuiApiServer:
    def __init__(self, bus: EventBus, *, host: str = "127.0.0.1", port: int = 8765,
                 risk=None, connection: str = "SIMULATION", bot_state: str = "RUNNING",
                 clock: Clock | None = None) -> None:
        self._bus = bus
        self.host = host
        self.port = port
        self._risk = risk
        self.connection = connection
        self.bot_state = bot_state
        self._clock = clock or RealClock()
        self._clients: set = set()
        self._marks: dict = {}
        self._pnl_series: list = []
        self._signal_series: dict = {}
        self._latency_ms = 0.0
        self._last_risk = {"decision": "", "reason": ""}
        self._server = None
        self._hb_task: asyncio.Task | None = None
        bus.subscribe_all(self._on_event)

    # -- serwer -------------------------------------------------------------- #
    async def start(self):
        self._server = await websockets.serve(self._handler, self.host, self.port)
        self._hb_task = asyncio.create_task(self._heartbeat())
        log.info("GUI API na ws://%s:%d/gui", self.host, self.port)
        return self._server

    async def stop(self) -> None:
        if self._hb_task:
            self._hb_task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws) -> None:
        self._clients.add(ws)
        try:
            await self._send_snapshot(ws)
            async for raw in ws:
                await self._on_client_message(raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    # -- wychodzące ---------------------------------------------------------- #
    def _broadcast(self, msg: dict) -> None:
        if self._clients:
            websockets.broadcast(self._clients, json.dumps(msg))

    async def _on_event(self, event: Event) -> None:
        if event.type == EventType.MARKET_TICK and isinstance(event.payload, MarketTick):
            tick = event.payload
            self._marks[tick.asset] = (tick.spot, tick.perp)
            self._latency_ms = tick.data_lag_ms

        msg = event_to_gui(event, self._marks)
        if msg is None:
            return
        if msg["type"] == "pnl":
            self._pnl_series.append({"ts": event.ts, "value": msg["net"]})
            self._pnl_series = self._pnl_series[-180:]
            msg["series"] = list(self._pnl_series)
        elif msg["type"] == "signal":
            hist = self._signal_series.setdefault(msg["asset"], [])
            hist.append({"ts": event.ts, "fairBasisBps": msg["fairBasisBps"],
                         "observedBasisBps": msg["observedBasisBps"]})
            self._signal_series[msg["asset"]] = hist[-180:]
            msg["series"] = list(self._signal_series[msg["asset"]])
        self._broadcast(msg)

        # po decyzji ryzyka dorzuć zaktualizowaną migawkę ryzyka
        if event.type in (EventType.RISK_APPROVED, EventType.RISK_REJECTED) and self._risk:
            dec = (event.payload or {}).get("decision")
            if dec is not None:
                self._last_risk = {"decision": event.type.value, "reason": dec.reason}
            self._broadcast(self._risk_msg())

    def _status_msg(self) -> dict:
        now = datetime.datetime.fromtimestamp(self._clock.now(), datetime.timezone.utc)
        return {"type": "status", "botState": self.bot_state, "connection": self.connection,
                "latencyMs": round(self._latency_ms), "serverTimeUtc": now.strftime("%H:%M:%S")}

    def _risk_msg(self) -> dict:
        r = self._risk
        c = r.config
        return {"type": "risk",
                "dailyLoss": {"used": max(0.0, -r.realized_pnl_today), "limit": c.max_daily_loss_usd},
                "perAssetExposure": [{"asset": a.value, "used": v, "limit": c.max_asset_exposure_usd}
                                     for a, v in r.exposure.items()],
                "openPositions": {"used": r.open_positions, "limit": c.max_open_positions},
                "marginBuffer": 1.0,
                "lastDecision": self._last_risk["decision"],
                "reason": self._last_risk["reason"]}

    async def _send_snapshot(self, ws) -> None:
        await ws.send(json.dumps(self._status_msg()))
        if self._risk:
            await ws.send(json.dumps(self._risk_msg()))

    async def _heartbeat(self) -> None:
        try:
            while True:
                self._broadcast(self._status_msg())
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    # -- przychodzące (komendy operatora) ----------------------------------- #
    async def _on_client_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if msg.get("type") != "command":
            return
        action = msg.get("action")
        ts = self._clock.now()
        await self._bus.publish(Event(EventType.OPERATOR_COMMAND, ts, "gui",
                                      payload={"action": action}))
        if action == "kill":
            self.bot_state = "KILLED"
            await self._bus.publish(Event(EventType.KILL_SWITCH, ts, "gui",
                                          payload={"reason": "operator kill"}))
        elif action == "flatten":
            await self._bus.publish(Event(EventType.FLATTEN, ts, "gui",
                                          payload={"reason": "operator flatten"}))
        elif action == "pause":
            self.bot_state = "PAUSED"
        elif action == "resume":
            self.bot_state = "RUNNING"
        self._broadcast(self._status_msg())
