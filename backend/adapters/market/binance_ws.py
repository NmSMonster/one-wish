"""BinanceWsSource — realne dane rynkowe ze STREAMÓW WebSocket (read-only).

Zastępuje polling REST (2 s) pushem z giełdy: spot bookTicker + perp bookTicker
+ markPrice (mark/index/funding/next-settlement). Niższa latencja, mniej zapytań
(zero rate-limit na odczyt) i świeższy forward funding — dokładnie to, czego
potrzebuje detektor carry.

Architektura jak w live adapterze: CAŁA logika składania ticków jest czystą,
deterministyczną klasą (`TickAssembler`) testowaną offline; jedyny styk I/O to
pętla `websockets.connect` w `ticks()` z reconnectem (backoff wykładniczy).
Świeżość danych pilnują istniejące mechanizmy (MarketDataAdapter STALE_FEED +
heartbeat Monitora) — źródło nie dubluje tej odpowiedzialności.

Strumienie (combined stream, envelope {"stream": ..., "data": {...}}):
- spot:    wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/...
- futures: wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/btcusdt@markPrice@1s/...
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from ...core.types import ASSETS, BINANCE_SYMBOL, Asset, MarketTick
from .base import MarketSource

log = logging.getLogger("onewish.binance_ws")

SPOT_WS_BASE = "wss://stream.binance.com:9443/stream"
FUT_WS_BASE = "wss://fstream.binance.com/stream"


def spot_stream_url(assets: tuple[Asset, ...]) -> str:
    streams = "/".join(f"{BINANCE_SYMBOL[a].lower()}@bookTicker" for a in assets)
    return f"{SPOT_WS_BASE}?streams={streams}"


def fut_stream_url(assets: tuple[Asset, ...]) -> str:
    parts = []
    for a in assets:
        sym = BINANCE_SYMBOL[a].lower()
        parts.append(f"{sym}@bookTicker")
        parts.append(f"{sym}@markPrice@1s")
    return f"{FUT_WS_BASE}?streams={'/'.join(parts)}"


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Wykładniczy backoff reconnectu: 1,2,4,8,... z sufitem (bez jitteru —
    deterministyczne w testach; dwa połączenia i tak startują niezależnie)."""
    return min(cap, base * (2.0 ** max(0, attempt - 1)))


@dataclass
class _AssetState:
    spot_bid: float = 0.0
    spot_ask: float = 0.0
    spot_bid_qty: float = 0.0
    spot_ask_qty: float = 0.0
    perp_bid: float = 0.0
    perp_ask: float = 0.0
    perp_bid_qty: float = 0.0
    perp_ask_qty: float = 0.0
    mark: float = 0.0
    index: float = 0.0
    funding_rate: float = 0.0
    interest_rate: float = 0.0
    next_funding_ts: float = 0.0
    event_ts: float = 0.0        # najnowszy czas eventu giełdy (E, sekundy)
    last_emit: float = 0.0

    @property
    def complete(self) -> bool:
        return (self.spot_bid > 0 and self.spot_ask > 0
                and self.perp_bid > 0 and self.perp_ask > 0
                and self.mark > 0 and self.index > 0 and self.next_funding_ts > 0)


class TickAssembler:
    """Czysta logika: wiadomość streamu → stan per aktywo → MarketTick (lub None).

    Tick emitowany, gdy stan jest KOMPLETNY (spot book + perp book + markPrice)
    i minął `min_emit_interval` od poprzedniej emisji (throttle — downstream nie
    potrzebuje częściej, a bookTicker potrafi strzelać setki razy na sekundę)."""

    def __init__(self, assets: tuple[Asset, ...] = ASSETS,
                 min_emit_interval: float = 0.5) -> None:
        self.min_emit_interval = min_emit_interval
        self._by_symbol = {BINANCE_SYMBOL[a]: a for a in assets}
        self._state: dict[Asset, _AssetState] = {a: _AssetState() for a in assets}

    def on_message(self, raw: str | dict, *, market: str,
                   now: float | None = None) -> MarketTick | None:
        """`market` = "spot" | "fut" (z którego połączenia przyszła wiadomość)."""
        now = time.time() if now is None else now
        try:
            envelope = json.loads(raw) if isinstance(raw, str) else raw
            data = envelope.get("data", envelope)
            symbol = data.get("s")
        except (ValueError, TypeError, AttributeError):
            return None
        asset = self._by_symbol.get(symbol)
        if asset is None:
            return None
        st = self._state[asset]

        etype = data.get("e")
        if etype == "markPriceUpdate":
            self._apply_mark(st, data)
        elif "b" in data and "a" in data:            # bookTicker (spot nie ma pola "e")
            self._apply_book(st, data, market)
        else:
            return None

        ev_ms = data.get("E")
        if ev_ms is not None:
            st.event_ts = max(st.event_ts, float(ev_ms) / 1000.0)

        return self._maybe_emit(asset, st, now)

    @staticmethod
    def _apply_book(st: _AssetState, d: dict, market: str) -> None:
        bid, ask = float(d["b"]), float(d["a"])
        bq = float(d.get("B", 0.0) or 0.0)
        aq = float(d.get("A", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            return
        if market == "spot":
            st.spot_bid, st.spot_ask, st.spot_bid_qty, st.spot_ask_qty = bid, ask, bq, aq
        else:
            st.perp_bid, st.perp_ask, st.perp_bid_qty, st.perp_ask_qty = bid, ask, bq, aq

    @staticmethod
    def _apply_mark(st: _AssetState, d: dict) -> None:
        st.mark = float(d.get("p", 0.0) or 0.0)
        st.index = float(d.get("i", 0.0) or 0.0)
        # "r" w markPriceUpdate to ESTYMOWANY funding bieżącego okresu, liczony
        # przez giełdę na żywo — to jest dokładnie forward funding dla carry
        # (lepszy niż nasza rekonstrukcja premia+clamp, bo zawiera interest rate).
        st.funding_rate = float(d.get("r", 0.0) or 0.0)
        next_ms = d.get("T")
        if next_ms:
            st.next_funding_ts = float(next_ms) / 1000.0

    def _maybe_emit(self, asset: Asset, st: _AssetState, now: float) -> MarketTick | None:
        if not st.complete:
            return None
        if (now - st.last_emit) < self.min_emit_interval:
            return None
        st.last_emit = now

        spot = (st.spot_bid + st.spot_ask) / 2.0
        perp = (st.perp_bid + st.perp_ask) / 2.0
        # głębokość: notional top-of-book (średnia bid/ask) — spójna semantyka
        # z REST-owym depth_usd (dostępny nominał przy topie księgi)
        spot_depth = (st.spot_bid_qty * st.spot_bid + st.spot_ask_qty * st.spot_ask) / 2.0
        perp_depth = (st.perp_bid_qty * st.perp_bid + st.perp_ask_qty * st.perp_ask) / 2.0
        lag_ms = max(0.0, (now - st.event_ts) * 1000.0) if st.event_ts > 0 else 0.0

        return MarketTick(
            asset=asset, ts=now, spot=spot, perp=perp, index=st.index,
            funding_rate=st.funding_rate,
            predicted_funding=st.funding_rate,   # "r" ze streamu = żywa estymata forward
            next_funding_ts=st.next_funding_ts,
            spot_bid=st.spot_bid, spot_ask=st.spot_ask,
            perp_bid=st.perp_bid, perp_ask=st.perp_ask,
            spot_depth_usd=spot_depth, perp_depth_usd=perp_depth,
            data_lag_ms=lag_ms, mark_price=st.mark, interest_rate=st.interest_rate,
        )


@dataclass
class BinanceWsSource(MarketSource):
    """Źródło ticków ze streamów WS. Dwa połączenia (spot + futures) zasilają
    wspólny assembler; `ticks()` oddaje złożone MarketTicki. Reconnect z
    backoffem; po `max_ticks` (opcjonalnym) kończy — do smoke-testów."""
    assets: tuple[Asset, ...] = ASSETS
    min_emit_interval: float = 0.5
    max_ticks: int | None = None
    reconnect_base_s: float = 1.0
    reconnect_cap_s: float = 30.0
    # nadpisywalne URL-e: testy E2E celują w LOKALNY symulator giełdy (prawdziwy
    # serwer WS) — weryfikacja całego stacku sieciowego bez dostępu do Binance
    spot_url: str | None = None
    fut_url: str | None = None
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)

    async def _pump(self, url: str, market: str, assembler: TickAssembler) -> None:
        import websockets
        attempt = 0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    attempt = 0
                    log.info("WS połączony (%s): %s", market, url.split("?")[0])
                    async for raw in ws:
                        # ramki Binance są tekstowe; bytes (rzadko) dekodujemy defensywnie
                        text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                        tick = assembler.on_message(text, market=market)
                        if tick is not None:
                            await self._queue.put(tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — sieć bywa brzydka; reconnect
                attempt += 1
                delay = backoff_delay(attempt, self.reconnect_base_s, self.reconnect_cap_s)
                log.warning("WS %s padł (%s) — reconnect za %.0fs (próba %d)",
                            market, exc, delay, attempt)
                await asyncio.sleep(delay)

    async def ticks(self) -> AsyncIterator[MarketTick]:
        assembler = TickAssembler(self.assets, self.min_emit_interval)
        pumps = [
            asyncio.create_task(self._pump(self.spot_url or spot_stream_url(self.assets),
                                           "spot", assembler)),
            asyncio.create_task(self._pump(self.fut_url or fut_stream_url(self.assets),
                                           "fut", assembler)),
        ]
        emitted = 0
        try:
            while True:
                yield await self._queue.get()
                emitted += 1
                if self.max_ticks is not None and emitted >= self.max_ticks:
                    return
        finally:
            for p in pumps:
                p.cancel()
