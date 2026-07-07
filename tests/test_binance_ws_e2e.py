"""E2E ścieżki WebSocket na LOKALNYM symulatorze giełdy (prawdziwe I/O, zero Binance).

Domyka lukę „WS niezweryfikowany": startujemy prawdziwe serwery WebSocket
(websockets.serve) mówiące formatem streamów Binance i celujemy w nie
`BinanceWsSource` — czyli testujemy CAŁY stack produkcyjny (connect przez
bibliotekę sieciową → iteracja wiadomości → assembler → MarketTick → pipeline),
łącznie z reconnectem po zerwanym połączeniu. Po tym teście jedyną rzeczą,
której nie da się zweryfikować bez żywego Binance, jest sama łączność.
"""
import asyncio
import json
import time

import pytest
import websockets

from backend.adapters.exchange import PaperBrokerAdapter
from backend.adapters.market.binance_ws import BinanceWsSource
from backend.app.pipeline import Pipeline
from backend.core.bus import EventBus
from backend.core.clock import SimClock
from backend.core.events import EventType
from backend.core.types import Asset
from tests.test_runner import _generous

def _now_ms() -> int:
    # czas MUSI być bieżący: assembler stempluje ticki realnym zegarem, a detektor
    # odrzuca wejścia „za blisko rozliczenia" gdy next_funding_ts jest w przeszłości
    return int(time.time() * 1000)


def _spot_msg():
    return json.dumps({"stream": "btcusdt@bookTicker",
                       "data": {"u": 1, "s": "BTCUSDT",
                                "b": "59999", "B": "5.0", "a": "60001", "A": "5.0"}})


def _fut_msgs():
    now = _now_ms()
    return [
        json.dumps({"stream": "btcusdt@bookTicker",
                    "data": {"e": "bookTicker", "E": now, "s": "BTCUSDT",
                             "b": "60029", "B": "5.0", "a": "60031", "A": "5.0"}}),
        json.dumps({"stream": "btcusdt@markPrice@1s",
                    "data": {"e": "markPriceUpdate", "E": now, "s": "BTCUSDT",
                             "p": "60030", "i": "60000", "r": "0.0003",
                             "T": now + 3_600_000}}),
    ]


async def _serve(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


def test_ws_source_end_to_end_against_local_exchange_simulator():
    async def scenario():
        async def spot_handler(ws):
            for _ in range(50):
                await ws.send(_spot_msg())
                await asyncio.sleep(0.01)

        async def fut_handler(ws):
            for _ in range(50):
                for m in _fut_msgs():
                    await ws.send(m)
                await asyncio.sleep(0.01)

        try:
            spot_srv, spot_url = await _serve(spot_handler)
            fut_srv, fut_url = await _serve(fut_handler)
        except OSError as exc:
            pytest.skip(f"nie można bindować lokalnych serwerów WS: {exc}")

        src = BinanceWsSource(min_emit_interval=0.0, max_ticks=5,
                              spot_url=spot_url, fut_url=fut_url,
                              reconnect_base_s=0.05)
        ticks = []
        try:
            async for t in src.ticks():
                ticks.append(t)
        finally:
            spot_srv.close()
            fut_srv.close()
        return ticks

    ticks = asyncio.run(scenario())
    assert len(ticks) == 5
    t = ticks[0]
    assert t.asset == Asset.BTC
    assert t.spot == pytest.approx(60_000.0)
    assert t.perp == pytest.approx(60_030.0)
    assert t.predicted_funding == pytest.approx(0.0003)   # "r" z markPrice streamu
    assert t.spot_depth_usd > 100_000                     # top-of-book notional


def test_ws_source_reconnects_after_dropped_connection():
    """Serwer spot zrywa połączenie po pierwszej wiadomości — źródło MUSI wrócić
    (backoff) i dalej składać ticki. To jest zachowanie, na którym wisi cała
    niezawodność transportu WS w live."""
    async def scenario():
        connections = {"spot": 0}

        async def flaky_spot(ws):
            connections["spot"] += 1
            await ws.send(_spot_msg())
            if connections["spot"] == 1:
                return                                     # zerwij pierwsze połączenie
            for _ in range(50):
                await ws.send(_spot_msg())
                await asyncio.sleep(0.01)

        async def fut_handler(ws):
            for _ in range(100):
                for m in _fut_msgs():
                    await ws.send(m)
                await asyncio.sleep(0.01)

        try:
            spot_srv, spot_url = await _serve(flaky_spot)
            fut_srv, fut_url = await _serve(fut_handler)
        except OSError as exc:
            pytest.skip(f"nie można bindować lokalnych serwerów WS: {exc}")

        src = BinanceWsSource(min_emit_interval=0.0, max_ticks=8,
                              spot_url=spot_url, fut_url=fut_url,
                              reconnect_base_s=0.05, reconnect_cap_s=0.1)
        ticks = []
        try:
            async for t in src.ticks():
                ticks.append(t)
        finally:
            spot_srv.close()
            fut_srv.close()
        return ticks, connections["spot"]

    ticks, spot_connections = asyncio.run(scenario())
    assert spot_connections >= 2                          # reconnect faktycznie zaszedł
    assert len(ticks) == 8                                # strumień przeżył zerwanie


def test_full_pipeline_trades_from_ws_stream():
    """Najmocniejszy dowód offline: wiadomości WS (format Binance) → assembler →
    MarketDataAdapter → PEŁNY pipeline → otwarta pozycja delta-neutral. Funding
    r=3bps przechodzi progi carry (min 0.1bps, payback ~7 rozliczeń < 30)."""
    from backend.adapters.market.base import MarketDataAdapter

    async def scenario():
        async def spot_handler(ws):
            for _ in range(80):
                await ws.send(_spot_msg())
                await asyncio.sleep(0.005)

        async def fut_handler(ws):
            for _ in range(80):
                for m in _fut_msgs():
                    await ws.send(m)
                await asyncio.sleep(0.005)

        try:
            spot_srv, spot_url = await _serve(spot_handler)
            fut_srv, fut_url = await _serve(fut_handler)
        except OSError as exc:
            pytest.skip(f"nie można bindować lokalnych serwerów WS: {exc}")

        bus = EventBus()
        pipe = Pipeline(bus, risk_config=_generous(), broker=PaperBrokerAdapter(seed=1),
                        clock=SimClock())
        opened: list = []
        bus.subscribe(EventType.POSITION_OPENED, lambda e: opened.append(e.payload))

        src = BinanceWsSource(min_emit_interval=0.0, max_ticks=6,
                              spot_url=spot_url, fut_url=fut_url,
                              reconnect_base_s=0.05)
        adapter = MarketDataAdapter(src, bus, SimClock(), stale_after_s=1e12)
        try:
            await adapter.run()
        finally:
            spot_srv.close()
            fut_srv.close()
        return pipe, opened

    pipe, opened = asyncio.run(scenario())
    assert opened, "pipeline powinien otworzyć pozycję z ticków złożonych ze streamu WS"
    assert pipe.book.is_open(Asset.BTC)
    pos = pipe.book.position(Asset.BTC)
    assert pos.spot_qty > 0 and pos.perp_qty < 0          # long spot + short perp
    assert abs(pos.net_delta) < 1e-9
