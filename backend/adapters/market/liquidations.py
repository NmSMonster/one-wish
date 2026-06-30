"""LiquidationStream — strumień likwidacji Binance Futures (WebSocket).

Likwidacje (przymusowe zamknięcia lewarowanych pozycji) to najlepsze momenty
wejścia dla carry: kaskada odrywa perp od indeksu (duża dyslokacja basis), więc
wchodzimy po lepszej cenie. Strumień `!forceOrder@arr` (wss) pcha każdą
likwidację całego rynku; filtrujemy do naszych aktywów.

Side liczymy zgodnie z konwencją Binance: SELL = zlikwidowano LONGA (przymusowa
sprzedaż), BUY = zlikwidowano SHORTA.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from ...core.types import ASSETS, BINANCE_SYMBOL, Asset, Side

log = logging.getLogger("onewish.liquidations")

LIQ_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


@dataclass
class Liquidation:
    asset: Asset
    ts: float
    side: Side          # SELL = long zlikwidowany; BUY = short zlikwidowany
    price: float
    qty: float
    notional_usd: float


def parse_liquidation(msg: dict, symbols: dict) -> Liquidation | None:
    o = msg.get("o") or {}
    asset = symbols.get(o.get("s"))
    if asset is None:
        return None
    side = Side.SELL if o.get("S") == "SELL" else Side.BUY
    price = float(o.get("ap") or o.get("p") or 0.0)
    qty = float(o.get("q") or 0.0)
    ts = float(o.get("T", msg.get("E", 0))) / 1000.0
    return Liquidation(asset=asset, ts=ts, side=side, price=price, qty=qty,
                       notional_usd=price * qty)


class LiquidationStream:
    def __init__(self, *, assets: tuple[Asset, ...] = ASSETS, on_liquidation=None,
                 max_events: int | None = None, reconnect_delay: float = 2.0) -> None:
        self.symbols = {BINANCE_SYMBOL[a]: a for a in assets}
        self.on_liquidation = on_liquidation
        self.max_events = max_events
        self.reconnect_delay = reconnect_delay
        self.count = 0

    async def run(self) -> None:
        import websockets
        while True:
            try:
                async with websockets.connect(LIQ_WS, ping_interval=20) as ws:
                    log.info("Podłączono do strumienia likwidacji.")
                    async for raw in ws:
                        try:
                            liq = parse_liquidation(json.loads(raw), self.symbols)
                        except (ValueError, TypeError):
                            continue
                        if liq is None:
                            continue
                        self.count += 1
                        if self.on_liquidation is not None:
                            self.on_liquidation(liq)
                        if self.max_events is not None and self.count >= self.max_events:
                            return
            except Exception as exc:  # noqa: BLE001 — drop połączenia → reconnect
                # CancelledError dziedziczy z BaseException (3.8+), więc tu NIE wpada
                # i poprawnie propaguje przy anulowaniu zadania (czysty shutdown).
                log.warning("Strumień likwidacji zerwany (%s) — reconnect za %ss",
                            exc, self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)
