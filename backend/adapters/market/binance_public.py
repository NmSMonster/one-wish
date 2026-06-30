"""BinancePublicSource — realne dane rynkowe Binance w trybie READ-ONLY.

Korzysta wyłącznie z publicznych endpointów REST (bez kluczy API): book ticker
spot, book ticker perp oraz premiumIndex (mark/index/funding). Łączy je w
MarketTick per aktywo. Blokujące zapytania HTTP wykonywane są w executorze, żeby
nie blokować pętli asyncio.

To jest jedyna część M3, która wymaga sieci; testy używają SyntheticSource/Replay.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator

from ...core.types import ASSETS, BINANCE_SYMBOL, Asset, MarketTick
from .base import MarketSource

log = logging.getLogger("onewish.binance")

SPOT_BASE = "https://api.binance.com"
FUT_BASE = "https://fapi.binance.com"


def predict_funding(mark_price: float, index_price: float, interest_rate: float,
                    cap: float = 0.0005) -> float:
    """Szacowany PRZYSZŁY funding wg wzoru Binance:

        funding = premia + clamp(interest_rate − premia, ±cap)

    gdzie premia = (mark − index) / index. To jest funding, który DOPIERO
    zainkasujemy, a nie ostatni rozliczony (lastFundingRate). Kluczowe dla carry.
    """
    if index_price <= 0:
        return 0.0
    premium = (mark_price - index_price) / index_price
    return premium + max(-cap, min(cap, interest_rate - premium))


def _http_get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"User-Agent": "one-wish/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_funding_history(symbol: str, limit: int = 100, timeout: float = 8.0) -> list[dict]:
    """Historia rozliczonych stawek funding (reżim) z /fapi/v1/fundingRate."""
    return _http_get_json(FUT_BASE + f"/fapi/v1/fundingRate?symbol={symbol}&limit={limit}", timeout)


def fetch_funding_history_paged(symbol: str, pages: int = 6, per_page: int = 1000,
                                timeout: float = 8.0) -> list[dict]:
    """Pobiera dłuższą historię funding cofając się w czasie (endTime), żeby werdykt
    obejmował różne reżimy (~rok). Binance oddaje ~200/zapytanie niezależnie od limit,
    więc cofamy się stroną po stronie. Zwraca chronologicznie rosnąco."""
    items: list[dict] = []
    end_time: int | None = None
    seen_oldest: int | None = None
    for _ in range(pages):
        url = FUT_BASE + f"/fapi/v1/fundingRate?symbol={symbol}&limit={per_page}"
        if end_time is not None:
            url += f"&endTime={end_time}"
        batch = _http_get_json(url, timeout)
        if not batch:
            break
        oldest = int(batch[0]["fundingTime"])
        if seen_oldest is not None and oldest >= seen_oldest:
            break                                  # brak postępu wstecz → koniec danych
        seen_oldest = oldest
        items = batch + items                      # starsze na początek
        end_time = oldest - 1
    return items


class BinancePublicSource(MarketSource):
    def __init__(
        self,
        *,
        assets: tuple[Asset, ...] = ASSETS,
        poll_interval: float = 2.0,
        timeout: float = 8.0,
        depth_usd_default: float = 500_000.0,
        fetch_depth: bool = False,
        depth_levels: int = 10,
        fetch_extras: bool = False,
        slow_every: int = 1,
        max_workers: int = 8,
        max_cycles: int | None = None,
    ) -> None:
        self.assets = assets
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.depth_usd_default = depth_usd_default
        self.fetch_depth = fetch_depth      # True = realna głębokość z orderbooka (uczciwa walidacja)
        self.depth_levels = depth_levels
        self.fetch_extras = fetch_extras    # True = open interest (dodatkowe zapytanie/aktywo)
        # Ceny (3 tanie zapytania market-wide) pobieramy co cykl. OI/głębokość są
        # wolnozmienne i drogie (zapytanie per aktywo) → odświeżamy co `slow_every`
        # cykli i cache'ujemy między nimi. slow_every=1 = stare zachowanie (co cykl).
        self.slow_every = max(1, slow_every)
        self._max_workers = max(1, max_workers)
        self.max_cycles = max_cycles
        self._symbols = {BINANCE_SYMBOL[a]: a for a in assets}
        self._oi_coins: dict[str, float] = {}                  # cache OI w coinach (×mark = USD)
        self._depth_cache: dict[str, tuple[float, float]] = {}  # cache (spot_depth_usd, perp_depth_usd)

    def _depth_usd(self, levels: list) -> float:
        """Suma nominału (cena×ilość) z górnych poziomów orderbooka."""
        return sum(float(p) * float(q) for p, q in levels[:self.depth_levels])

    def _get(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": "one-wish/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)

    def _fetch_oi_coins(self, symbol: str) -> float:
        oi = self._get(FUT_BASE + f"/fapi/v1/openInterest?symbol={symbol}")
        return float(oi.get("openInterest", 0.0))

    def _fetch_depth_usd(self, symbol: str) -> tuple[float, float]:
        sd = self._get(SPOT_BASE + f"/api/v3/depth?symbol={symbol}&limit={self.depth_levels}")
        pd = self._get(FUT_BASE + f"/fapi/v1/depth?symbol={symbol}&limit={self.depth_levels}")
        spot_depth = self._depth_usd(sd.get("asks", []))   # kupujemy spot → asks
        perp_depth = self._depth_usd(pd.get("bids", []))   # sprzedajemy perp → bids
        return spot_depth, perp_depth

    def _refresh_heavy(self) -> None:
        """Odświeża wolnozmienne dane (OI + głębokość) równolegle i wpisuje do cache.

        Wszystkie zapytania per-aktywo lecą współbieżnie (ThreadPool) zamiast w
        sekwencji — cykl odświeżenia nie blokuje się na sumie latencji. Przy błędzie
        sieci zachowujemy poprzednią wartość z cache (stale lepsze niż reset do
        placeholdera) — wpis aktualizujemy tylko, gdy zapytanie się powiodło.
        """
        symbols = list(self._symbols)

        def work(symbol: str) -> tuple[str, float | None, tuple[float, float] | None]:
            oi: float | None = None
            depth: tuple[float, float] | None = None
            if self.fetch_extras:
                try:
                    oi = self._fetch_oi_coins(symbol)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
                    log.warning("Brak open interest dla %s — zachowuję poprzednią", symbol)
            if self.fetch_depth:
                try:
                    depth = self._fetch_depth_usd(symbol)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
                    log.warning("Brak realnej głębokości dla %s — zachowuję poprzednią", symbol)
            return symbol, oi, depth

        workers = min(self._max_workers, len(symbols)) or 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(work, symbols))
        for symbol, oi, depth in results:
            if oi is not None:
                self._oi_coins[symbol] = oi
            if depth is not None:
                self._depth_cache[symbol] = depth

    def _fetch_once(self, refresh_heavy: bool = True) -> list[MarketTick]:
        spot_book = {r["symbol"]: r for r in self._get(SPOT_BASE + "/api/v3/ticker/bookTicker")}
        prem = {r["symbol"]: r for r in self._get(FUT_BASE + "/fapi/v1/premiumIndex")}
        fut_book = {r["symbol"]: r for r in self._get(FUT_BASE + "/fapi/v1/ticker/bookTicker")}
        now_ms = time.time() * 1000.0

        if refresh_heavy and (self.fetch_extras or self.fetch_depth):
            self._refresh_heavy()

        ticks: list[MarketTick] = []
        for symbol, asset in self._symbols.items():
            sb = spot_book.get(symbol)
            pm = prem.get(symbol)
            fb = fut_book.get(symbol)
            if not (sb and pm and fb):
                log.warning("Brak danych dla %s — pomijam", symbol)
                continue

            spot_bid = float(sb["bidPrice"])
            spot_ask = float(sb["askPrice"])
            perp_bid = float(fb["bidPrice"])
            perp_ask = float(fb["askPrice"])
            spot = (spot_bid + spot_ask) / 2.0
            perp = (perp_bid + perp_ask) / 2.0
            index = float(pm["indexPrice"])
            funding = float(pm.get("lastFundingRate", 0.0) or 0.0)   # ostatni rozliczony (historia)
            mark = float(pm.get("markPrice", perp) or perp)
            interest = float(pm.get("interestRate", 0.0) or 0.0)
            predicted = predict_funding(mark, index, interest)       # forward (to, co zainkasujemy)
            next_funding_ts = float(pm.get("nextFundingTime", 0.0)) / 1000.0
            server_ms = float(pm.get("time", now_ms))

            # OI USD = świeży mark × cache'owane coiny (mark zmienia się co cykl,
            # wolumen OI wolno — liczymy na bieżącym marku dla aktualności).
            open_interest_usd = self._oi_coins.get(symbol, 0.0) * mark if self.fetch_extras else 0.0

            if self.fetch_depth:
                spot_depth, perp_depth = self._depth_cache.get(
                    symbol, (self.depth_usd_default, self.depth_usd_default)
                )
            else:
                spot_depth = perp_depth = self.depth_usd_default

            ticks.append(
                MarketTick(
                    asset=asset,
                    ts=server_ms / 1000.0,
                    spot=spot,
                    perp=perp,
                    index=index,
                    funding_rate=funding,
                    predicted_funding=predicted,
                    next_funding_ts=next_funding_ts,
                    spot_bid=spot_bid,
                    spot_ask=spot_ask,
                    perp_bid=perp_bid,
                    perp_ask=perp_ask,
                    spot_depth_usd=spot_depth,
                    perp_depth_usd=perp_depth,
                    data_lag_ms=max(0.0, now_ms - server_ms),
                    mark_price=mark,
                    open_interest_usd=open_interest_usd,
                    interest_rate=interest,
                )
            )
        return ticks

    async def ticks(self) -> AsyncIterator[MarketTick]:
        loop = asyncio.get_event_loop()
        cycles = 0
        while True:
            refresh_heavy = (cycles % self.slow_every == 0)   # cykl 0 zawsze odświeża
            try:
                batch = await loop.run_in_executor(None, self._fetch_once, refresh_heavy)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("Błąd pobierania danych Binance: %s", exc)
                batch = []
            for tick in batch:
                yield tick
            cycles += 1
            if self.max_cycles is not None and cycles >= self.max_cycles:
                break
            await asyncio.sleep(self.poll_interval)
