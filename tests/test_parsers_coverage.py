"""Testy czystych parserów odpowiedzi giełdy (luki wskazane pomiarem pokrycia).

Dwa parsery były bez testów mimo krytyczności:
- `quantize._parse` (filtry symbolu z exchangeInfo): błąd tutaj = WSZYSTKIE
  zlecenia live źle skwantyzowane (reject albo dust psujący delta-neutralność),
- `liquidations.parse_liquidation` (timing wejścia na kaskadach likwidacji).
Fixture'y w realnych kształtach odpowiedzi Binance.
"""
import pytest

from backend.adapters.market.liquidations import parse_liquidation
from backend.core.types import Asset, Side
from backend.execution.quantize import _parse


def _exchange_info() -> dict:
    return {"symbols": [
        {"symbol": "BTCUSDT", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "0.1"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
            {"filterType": "NOTIONAL", "minNotional": "5.0"},
        ]},
        {"symbol": "DOGEUSDT", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
            # wariant futures: MIN_NOTIONAL z polem "notional"
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]},
        {"symbol": "SPOZAUNIWERSUM", "filters": []},
    ]}


def test_parse_exchange_info_extracts_filters():
    out = _parse(_exchange_info(), (Asset.BTC, Asset.DOGE))
    btc = out[Asset.BTC]
    assert btc.symbol == "BTCUSDT"
    assert btc.tick_size == pytest.approx(0.10)
    assert btc.step_size == pytest.approx(0.00001)
    assert btc.min_qty == pytest.approx(0.00001)
    assert btc.min_notional == pytest.approx(5.0)
    doge = out[Asset.DOGE]
    assert doge.min_notional == pytest.approx(5.0)     # wariant "notional" (futures)
    assert doge.step_size == pytest.approx(1.0)


def test_parse_exchange_info_skips_unrequested_and_unknown():
    out = _parse(_exchange_info(), (Asset.BTC,))
    assert Asset.DOGE not in out                        # nie prosiliśmy
    assert len(out) == 1                                # obcy symbol pominięty


def test_parse_exchange_info_tolerates_missing_filters():
    out = _parse({"symbols": [{"symbol": "BTCUSDT", "filters": []}]}, (Asset.BTC,))
    f = out[Asset.BTC]
    assert f.tick_size == 0.0 and f.min_notional == 0.0
    # zerowe filtry = passthrough (bez kwantyzacji), nie crash
    assert f.q_price(123.456) == 123.456
    assert f.ok(1.0, 1.0) is True


def _force_order(sym="BTCUSDT", side="SELL", ap="60000", q="0.5", t=1_700_000_000_000):
    return {"e": "forceOrder", "E": t, "o": {"s": sym, "S": side, "ap": ap,
                                             "p": "59990", "q": q, "T": t}}


def test_parse_liquidation_long_flush():
    symbols = {"BTCUSDT": Asset.BTC}
    liq = parse_liquidation(_force_order(), symbols)
    assert liq.asset == Asset.BTC
    assert liq.side == Side.SELL                        # SELL = zlikwidowany LONG
    assert liq.price == pytest.approx(60_000.0)
    assert liq.notional_usd == pytest.approx(30_000.0)
    assert liq.ts == pytest.approx(1_700_000_000.0)     # ms → s


def test_parse_liquidation_short_squeeze_and_price_fallback():
    symbols = {"BTCUSDT": Asset.BTC}
    msg = _force_order(side="BUY", ap="")               # brak avg price → fallback "p"
    liq = parse_liquidation(msg, symbols)
    assert liq.side == Side.BUY                         # BUY = zlikwidowany SHORT
    assert liq.price == pytest.approx(59_990.0)


def test_parse_liquidation_foreign_symbol_ignored():
    assert parse_liquidation(_force_order(sym="OBCYUSDT"), {"BTCUSDT": Asset.BTC}) is None
    assert parse_liquidation({}, {"BTCUSDT": Asset.BTC}) is None
