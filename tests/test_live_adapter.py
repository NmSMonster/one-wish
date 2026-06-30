"""Testy M12: realny adapter jest twardo zablokowany (bezpieczeństwo)."""
import asyncio

import pytest

from backend.adapters.exchange.binance_live import BinanceLiveAdapter, LiveTradingBlocked
from backend.core.types import Asset, Leg, OrderRequest, OrderStatus, OrderType, Side


def _req(notional_price=100.0, qty=0.1) -> OrderRequest:
    return OrderRequest("c1", Asset.BTC, Leg.SPOT, Side.BUY, OrderType.MARKET,
                        notional_price, qty, 1.0)


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
