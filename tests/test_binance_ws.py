"""Testy źródła WS: czysty TickAssembler (parsowanie/składanie/throttle) + URL-e.

Zero sieci: assembler dostaje surowe wiadomości streamów (dokładnie w formacie
combined stream Binance) i musi z nich złożyć poprawny MarketTick.
"""
import json

import pytest

from backend.adapters.market.binance_ws import (
    TickAssembler,
    backoff_delay,
    fut_stream_url,
    spot_stream_url,
)
from backend.core.types import ASSETS, Asset


def _spot_book(sym="BTCUSDT", bid="59999", ask="60001", bq="2.0", aq="2.0") -> str:
    # spot bookTicker NIE ma pola "e" ani "E" — envelope combined stream
    return json.dumps({"stream": f"{sym.lower()}@bookTicker",
                       "data": {"u": 1, "s": sym, "b": bid, "B": bq, "a": ask, "A": aq}})


def _fut_book(sym="BTCUSDT", bid="60029", ask="60031", bq="3.0", aq="3.0", ev_ms=1_000_000) -> str:
    return json.dumps({"stream": f"{sym.lower()}@bookTicker",
                       "data": {"e": "bookTicker", "E": ev_ms, "s": sym,
                                "b": bid, "B": bq, "a": ask, "A": aq}})


def _mark(sym="BTCUSDT", mark="60030", index="60000", rate="0.0002",
          next_ms=2_000_000, ev_ms=1_000_100) -> str:
    return json.dumps({"stream": f"{sym.lower()}@markPrice@1s",
                       "data": {"e": "markPriceUpdate", "E": ev_ms, "s": sym,
                                "p": mark, "i": index, "r": rate, "T": next_ms}})


def _feed_complete(asm: TickAssembler, now=1000.0):
    assert asm.on_message(_spot_book(), market="spot", now=now) is None      # niekompletny
    assert asm.on_message(_fut_book(), market="fut", now=now) is None        # niekompletny
    return asm.on_message(_mark(), market="fut", now=now)                    # komplet → tick


def test_assembles_tick_from_three_streams():
    tick = _feed_complete(TickAssembler())
    assert tick is not None and tick.asset == Asset.BTC
    assert tick.spot == pytest.approx(60_000.0)          # mid spota
    assert tick.perp == pytest.approx(60_030.0)          # mid perpa
    assert tick.index == pytest.approx(60_000.0)
    assert tick.basis_bps == pytest.approx(5.0, abs=0.01)
    assert tick.funding_rate == pytest.approx(0.0002)
    assert tick.predicted_funding == pytest.approx(0.0002)  # "r" = żywa estymata giełdy
    assert tick.next_funding_ts == pytest.approx(2_000.0)  # ms → s
    assert tick.mark_price == pytest.approx(60_030.0)


def test_depth_is_top_of_book_notional():
    tick = _feed_complete(TickAssembler())
    # spot: (2.0×59999 + 2.0×60001)/2 = 120000; perp: (3×60029 + 3×60031)/2 = 180090
    assert tick.spot_depth_usd == pytest.approx(120_000.0)
    assert tick.perp_depth_usd == pytest.approx(180_090.0)


def test_throttle_suppresses_flood_then_allows():
    asm = TickAssembler(min_emit_interval=0.5)
    assert _feed_complete(asm, now=1000.0) is not None
    # zalew bookTickerów w tej samej chwili → cisza (throttle)
    assert asm.on_message(_spot_book(bid="60000"), market="spot", now=1000.1) is None
    assert asm.on_message(_fut_book(bid="60030"), market="fut", now=1000.3) is None
    # po odczekaniu interwału → kolejny tick
    assert asm.on_message(_spot_book(), market="spot", now=1000.6) is not None


def test_incomplete_state_never_emits():
    asm = TickAssembler()
    assert asm.on_message(_spot_book(), market="spot", now=1.0) is None
    assert asm.on_message(_mark(), market="fut", now=1.0) is None    # brak perp book
    asm2 = TickAssembler()
    assert asm2.on_message(_fut_book(), market="fut", now=1.0) is None
    assert asm2.on_message(_mark(), market="fut", now=1.0) is None   # brak spot book


def test_unknown_symbol_and_garbage_ignored():
    asm = TickAssembler()
    assert asm.on_message(_spot_book(sym="SHITUSDT"), market="spot", now=1.0) is None
    assert asm.on_message("nie-json{{{", market="spot", now=1.0) is None
    assert asm.on_message(json.dumps({"data": {"x": 1}}), market="fut", now=1.0) is None


def test_data_lag_from_exchange_event_time():
    asm = TickAssembler()
    # eventy giełdy @ t=1000.0s i 1000.1s; my składamy tick @ now=1000.35
    asm.on_message(_spot_book(), market="spot", now=1000.35)
    asm.on_message(_fut_book(ev_ms=1_000_000), market="fut", now=1000.35)
    tick = asm.on_message(_mark(ev_ms=1_000_100), market="fut", now=1000.35)
    assert tick.data_lag_ms == pytest.approx(250.0, abs=1.0)   # 1000.35 − 1000.1


def test_each_asset_tracked_independently():
    asm = TickAssembler()
    _feed_complete(asm, now=1000.0)                              # BTC kompletny
    # ETH ma tylko spot book → brak emisji dla ETH, BTC dalej działa
    assert asm.on_message(_spot_book(sym="ETHUSDT"), market="spot", now=1001.0) is None
    t = asm.on_message(_spot_book(), market="spot", now=1001.0)
    assert t is not None and t.asset == Asset.BTC


def test_stream_urls_cover_all_assets():
    su, fu = spot_stream_url(ASSETS), fut_stream_url(ASSETS)
    for a in ASSETS:
        sym = a.value.lower()
        assert f"{sym}usdt@bookTicker".lower() in su.lower()
        assert f"{sym}usdt@markPrice@1s".lower() in fu.lower()
    assert su.startswith("wss://stream.binance.com")
    assert fu.startswith("wss://fstream.binance.com")


def test_backoff_exponential_with_cap():
    assert backoff_delay(1) == 1.0
    assert backoff_delay(2) == 2.0
    assert backoff_delay(4) == 8.0
    assert backoff_delay(10) == 30.0                             # sufit
