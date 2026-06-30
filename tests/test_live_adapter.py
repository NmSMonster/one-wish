"""Testy M12: realny adapter — bramki bezpieczeństwa + transport na testnecie."""
import asyncio
import hashlib
import hmac

import pytest

from backend.adapters.exchange.binance_live import BinanceLiveAdapter, LiveTradingBlocked
from backend.core.types import Asset, Leg, OrderRequest, OrderStatus, OrderType, Side


def _req(notional_price=100.0, qty=0.1, leg=Leg.SPOT, side=Side.BUY, coid="c1") -> OrderRequest:
    return OrderRequest(coid, Asset.BTC, leg, side, OrderType.MARKET,
                        notional_price, qty, 1.0)


def _armed(*, testnet=True, transport=True, max_notional=1e9, allow_mainnet=False) -> BinanceLiveAdapter:
    a = BinanceLiveAdapter(live_enabled=True, api_key="k", api_secret="s",
                           transport_implemented=transport, testnet=testnet,
                           allow_mainnet=allow_mainnet, max_notional_usd=max_notional)
    a.arm("I_UNDERSTAND_REAL_MONEY")
    return a


def test_default_adapter_rejects_everything():
    a = BinanceLiveAdapter()
    res = asyncio.run(a.submit(_req()))
    assert res.status == OrderStatus.REJECTED
    assert "ZABLOKOWANY" in res.reason
    assert not a.is_armed


def test_arm_fails_when_live_disabled():
    a = BinanceLiveAdapter(live_enabled=False, api_key="k", api_secret="s")
    with pytest.raises(LiveTradingBlocked):
        a.arm("I_UNDERSTAND_REAL_MONEY")


def test_arm_fails_without_keys():
    a = BinanceLiveAdapter(live_enabled=True)
    with pytest.raises(LiveTradingBlocked):
        a.arm("I_UNDERSTAND_REAL_MONEY")


def test_arm_fails_with_wrong_confirm():
    a = BinanceLiveAdapter(live_enabled=True, api_key="k", api_secret="s")
    with pytest.raises(LiveTradingBlocked):
        a.arm("nope")


def test_armed_adapter_still_rejects_because_transport_off():
    a = BinanceLiveAdapter(live_enabled=True, api_key="k", api_secret="s")
    a.arm("I_UNDERSTAND_REAL_MONEY")
    assert a.is_armed
    res = asyncio.run(a.submit(_req()))
    assert res.status == OrderStatus.REJECTED
    assert "transport" in res.reason


def test_notional_limit_enforced_even_when_armed():
    a = BinanceLiveAdapter(live_enabled=True, api_key="k", api_secret="s", max_notional_usd=5.0)
    a.arm("I_UNDERSTAND_REAL_MONEY")
    res = asyncio.run(a.submit(_req(notional_price=100.0, qty=1.0)))  # 100$ > 5$
    assert res.status == OrderStatus.REJECTED
    assert "nominał" in res.reason


# -- transport na testnecie (stub _signed_request — zero sieci) -------------- #
def test_endpoints_select_testnet_vs_mainnet():
    t = BinanceLiveAdapter(testnet=True)
    m = BinanceLiveAdapter(testnet=False)
    assert "testnet" in t.spot_base and "testnet" in t.fut_base
    assert t.spot_base != m.spot_base and "testnet" not in m.fut_base


def test_sign_is_hmac_sha256():
    a = BinanceLiveAdapter(api_secret="topsecret")
    q = "symbol=BTCUSDT&side=BUY&timestamp=1"
    expected = hmac.new(b"topsecret", q.encode(), hashlib.sha256).hexdigest()
    assert a._sign(q) == expected


def test_mainnet_blocked_even_when_armed():
    a = _armed(testnet=False, allow_mainnet=False)
    res = asyncio.run(a.submit(_req()))
    assert res.status == OrderStatus.REJECTED
    assert "mainnet" in res.reason.lower()


def test_testnet_spot_order_parses_fills():
    a = _armed(testnet=True)
    captured = {}

    def fake(method, base, path, params):
        captured.update(method=method, base=base, path=path, params=params)
        return {"status": "FILLED", "executedQty": "0.10",
                "fills": [{"price": "100.0", "qty": "0.06", "commission": "0.004"},
                          {"price": "110.0", "qty": "0.04", "commission": "0.003"}]}
    a._signed_request = fake

    res = asyncio.run(a.submit(_req(notional_price=100.0, qty=0.1, leg=Leg.SPOT)))
    assert res.status == OrderStatus.FILLED
    assert len(res.fills) == 1
    f = res.fills[0]
    assert abs(f.qty - 0.10) < 1e-9
    # avg = (100*0.06 + 110*0.04) / 0.10 = 104.0
    assert abs(f.price - 104.0) < 1e-9
    assert abs(f.fee - 0.007) < 1e-9
    # poszło na endpoint spot testnet, jako POST, z idempotentnym coid
    assert captured["method"] == "POST"
    assert "testnet" in captured["base"] and captured["path"].endswith("/order")
    assert captured["params"]["newClientOrderId"] == "c1"
    assert captured["params"]["symbol"] == "BTCUSDT"


def test_testnet_perp_order_parses_avgprice():
    a = _armed(testnet=True)

    def fake(method, base, path, params):
        assert "fapi" in path                       # noga perp → endpoint futures
        return {"status": "FILLED", "executedQty": "0.10", "avgPrice": "120.0"}
    a._signed_request = fake

    res = asyncio.run(a.submit(_req(qty=0.1, leg=Leg.PERP, side=Side.SELL)))
    assert res.status == OrderStatus.FILLED
    assert abs(res.fills[0].price - 120.0) < 1e-9
    assert res.fills[0].leg == Leg.PERP and res.fills[0].side == Side.SELL


def test_zero_executed_qty_is_rejected():
    a = _armed(testnet=True)
    a._signed_request = lambda *args, **kw: {"status": "EXPIRED", "executedQty": "0", "fills": []}
    res = asyncio.run(a.submit(_req()))
    assert res.status == OrderStatus.REJECTED


def test_transport_error_is_handled_not_raised():
    a = _armed(testnet=True)

    def boom(*args, **kw):
        raise OSError("network down")
    a._signed_request = boom

    res = asyncio.run(a.submit(_req()))
    assert res.status == OrderStatus.REJECTED
    assert "transport" in res.reason


def test_notional_limit_blocks_before_transport():
    a = _armed(testnet=True, max_notional=5.0)
    called = {"n": 0}

    def fake(*args, **kw):
        called["n"] += 1
        return {"status": "FILLED", "executedQty": "1", "avgPrice": "100"}
    a._signed_request = fake

    res = asyncio.run(a.submit(_req(notional_price=100.0, qty=1.0)))   # 100$ > 5$
    assert res.status == OrderStatus.REJECTED and "nominał" in res.reason
    assert called["n"] == 0                          # transport NIE dotknięty


def test_reconcile_combines_spot_and_futures_open_orders():
    a = _armed(testnet=True)

    def fake(method, base, path, params):
        if "fapi" in path:
            return [{"symbol": "BTCUSDT", "orderId": 2}]
        return [{"symbol": "BTCUSDT", "orderId": 1}]
    a._signed_request = fake

    out = asyncio.run(a.reconcile())
    assert len(out) == 2
    assert {o["orderId"] for o in out} == {1, 2}


def test_reconcile_empty_when_transport_off():
    a = _armed(testnet=True, transport=False)
    assert asyncio.run(a.reconcile()) == []


def test_account_state_returns_spot_and_futures():
    a = _armed(testnet=True)
    a._signed_request = lambda method, base, path, params: (
        {"balances": []} if "account" in path else [{"asset": "USDT", "balance": "1000"}])
    state = asyncio.run(a.account_state())
    assert "spot" in state and "futures" in state
