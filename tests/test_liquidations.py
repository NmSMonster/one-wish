"""Testy: parsowanie strumienia likwidacji Binance."""
from backend.adapters.market.liquidations import Liquidation, parse_liquidation
from backend.core.types import Asset, Side

_SYMBOLS = {"BTCUSDT": Asset.BTC, "ETHUSDT": Asset.ETH}


def test_parse_long_liquidation():
    msg = {"e": "forceOrder", "E": 1568014460893,
           "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.5", "p": "60000", "ap": "60010",
                 "T": 1568014460893}}
    liq = parse_liquidation(msg, _SYMBOLS)
    assert isinstance(liq, Liquidation)
    assert liq.asset == Asset.BTC
    assert liq.side == Side.SELL                       # SELL = long zlikwidowany
    assert liq.price == 60010.0                         # avg price ma priorytet
    assert liq.notional_usd == 60010.0 * 0.5


def test_parse_short_liquidation():
    msg = {"o": {"s": "ETHUSDT", "S": "BUY", "q": "2", "p": "2000", "ap": "2001", "T": 1000}}
    liq = parse_liquidation(msg, _SYMBOLS)
    assert liq.asset == Asset.ETH and liq.side == Side.BUY
    assert liq.notional_usd == 2001.0 * 2


def test_parse_unknown_symbol_ignored():
    assert parse_liquidation({"o": {"s": "DOGEUSDT", "S": "SELL", "q": "1", "p": "1"}},
                             _SYMBOLS) is None


def test_parse_malformed_returns_none():
    assert parse_liquidation({}, _SYMBOLS) is None
